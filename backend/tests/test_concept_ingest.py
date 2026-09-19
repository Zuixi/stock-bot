"""概念成分采集编排：失败隔离 / 完整性门禁 / 映射统计（monkeypatch，不触 DB/网络）。

本文件钉的是 T4 的核心不变量：**任何抓取异常或半截页都不得表现为"成分退出板块"**——
既不删成员也不写 change 行。repo 层用 `_FakeRepo` 替身（真 repo 的差分语义在
tests/test_concept_repo.py 已单独钉过），故本文件留在默认 `uv run pytest` 门禁里。
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.repositories import concept_repo
from app.services import concept_service

# 默认两块板：BK0001 完整（total=2，客户端给 2 行）、BK0002 抓取失败（total=1）。
_DEFAULT_BOARDS: list[dict[str, Any]] = [
    {"board_code": "BK0001", "board_name": "A", "member_total": 2},
    {"board_code": "BK0002", "board_name": "B", "member_total": 1},
]
_DEFAULT_MEMBERS: dict[str, list[dict[str, Any]]] = {
    "BK0001": [
        {"symbol": "600000", "name": "浦发银行", "market_flag": 1},
        {"symbol": "600001", "name": "邯郸钢铁", "market_flag": 1},
    ],
}


class _FakeEm:
    """最小东财客户端替身：板块列表/成分/异常全部可注入。"""

    def __init__(
        self,
        boards: list[dict[str, Any]] | None = None,
        members: dict[str, list[dict[str, Any]]] | None = None,
        *,
        fail_on: str | None = None,
        fail_board_list: bool = False,
    ) -> None:
        self._boards = _DEFAULT_BOARDS if boards is None else boards
        self._members = _DEFAULT_MEMBERS if members is None else members
        self.fail_on = fail_on
        self.fail_board_list = fail_board_list
        self.member_calls: list[str] = []

    async def fetch_concept_boards(self) -> list[dict[str, Any]]:
        if self.fail_board_list:
            raise httpx.TransportError("board list boom")
        return self._boards

    async def fetch_concept_members(self, code: str) -> list[dict[str, Any]]:
        self.member_calls.append(code)
        if code == self.fail_on:
            raise httpx.TransportError("boom")
        return self._members.get(code, [])


class _FakeRepo:
    """记录写入的仓库替身：沿用真 repo 的差分语义，便于断言"某板一行未写"。

    `events` 记录调用顺序（boards → members → deactivate），`changes` 只由 add/remove 产生。
    """

    def __init__(self, stock_ids: dict[str, int] | None = None, fail_write_on: str | None = None):
        self.stock_ids = stock_ids or {}
        self.fail_write_on = fail_write_on
        self.boards_rows: list[dict[str, Any]] = []
        self.member_calls: list[tuple[str, list[dict[str, Any]]]] = []
        self.changes: list[dict[str, Any]] = []
        self.members: dict[str, dict[str, str]] = {}
        self.mapped_symbols: list[list[str]] = []
        self.deactivate_calls: list[set[str]] = []
        self.events: list[str] = []

    async def upsert_boards(self, db: Any, rows: list[dict[str, Any]], today: Any) -> int:
        self.boards_rows = list(rows)
        self.events.append("boards")
        return len(rows)

    async def symbol_to_stock_ids(self, db: Any, symbols: list[str]) -> dict[str, int]:
        self.mapped_symbols.append(list(symbols))
        return {s: self.stock_ids[s] for s in symbols if s in self.stock_ids}

    async def upsert_members(
        self, db: Any, board_code: str, rows: list[dict[str, Any]], today: Any
    ) -> tuple[int, int, int]:
        self.member_calls.append((board_code, list(rows)))
        self.events.append(f"members:{board_code}")
        if board_code == self.fail_write_on:
            raise RuntimeError("write boom")
        existing = self.members.get(board_code, {})
        seen = {row["symbol"]: row["stock_name"] for row in rows}
        added = [s for s in seen if s not in existing]
        removed = [s for s in existing if s not in seen]
        self.changes.extend(
            {"board_code": board_code, "symbol": s, "change_type": "add"} for s in added
        )
        self.changes.extend(
            {"board_code": board_code, "symbol": s, "change_type": "remove"} for s in removed
        )
        self.members[board_code] = seen
        return (len(added), len(seen) - len(added), len(removed))

    async def deactivate_missing_boards(self, db: Any, codes: set[str]) -> int:
        self.deactivate_calls.append(set(codes))
        self.events.append("deactivate")
        return 0


@pytest.fixture
def make_fake_repo(monkeypatch: pytest.MonkeyPatch) -> Any:
    """构造并注入仓库替身（service 以模块属性调用 `concept_repo.<fn>`，可直接替换）。"""

    def _make(
        stock_ids: dict[str, int] | None = None, fail_write_on: str | None = None
    ) -> _FakeRepo:
        repo = _FakeRepo(stock_ids, fail_write_on)
        monkeypatch.setattr(concept_repo, "upsert_boards", repo.upsert_boards)
        monkeypatch.setattr(concept_repo, "symbol_to_stock_ids", repo.symbol_to_stock_ids)
        monkeypatch.setattr(concept_repo, "upsert_members", repo.upsert_members)
        monkeypatch.setattr(
            concept_repo, "deactivate_missing_boards", repo.deactivate_missing_boards
        )
        return repo

    return _make


@pytest.fixture
def fake_repo(make_fake_repo: Any) -> _FakeRepo:
    return make_fake_repo()


async def test_ingest_isolates_single_board_failure(
    monkeypatch: pytest.MonkeyPatch, fake_repo: _FakeRepo
) -> None:
    """一个板块抓不到：跳过它（不删成员、不写 change），其余照常，且 failed_boards 上报。"""
    monkeypatch.setattr(concept_service, "_get_eastmoney", lambda: _FakeEm(fail_on="BK0002"))
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["boards"] == 2 and out["failed_boards"] == 1 and out["added"] == 2
    assert out["partial_boards"] == 0 and out["removed"] == 0
    # change 行按成员逐条记录（真 repo 语义），失败板必须一条都没有。
    assert {c["board_code"] for c in fake_repo.changes} == {"BK0001"}
    assert all(c["change_type"] == "add" for c in fake_repo.changes)
    assert [code for code, _ in fake_repo.member_calls] == ["BK0001"], "失败板不得进入写入路径"
    assert fake_repo.members == {"BK0001": {"600000": "浦发银行", "600001": "邯郸钢铁"}}


async def test_ingest_write_failure_is_isolated_to_that_board(
    monkeypatch: pytest.MonkeyPatch, make_fake_repo: Any
) -> None:
    """仓库层单板异常同样只跳该板：其余写成功，failed_boards 上报，不中断整批。"""
    repo = make_fake_repo(fail_write_on="BK0001")
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(
            boards=[
                {"board_code": "BK0001", "board_name": "A", "member_total": 1},
                {"board_code": "BK0002", "board_name": "B", "member_total": 1},
            ],
            members={
                "BK0001": [{"symbol": "600000", "name": "浦发银行"}],
                "BK0002": [{"symbol": "600001", "name": "邯郸钢铁"}],
            },
        ),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["failed_boards"] == 1 and out["added"] == 1
    assert [c["board_code"] for c in repo.changes] == ["BK0002"]


async def test_ingest_skips_partial_board_without_writing(
    monkeypatch: pytest.MonkeyPatch, make_fake_repo: Any
) -> None:
    """半截页（61/63）必须整板跳过：不得删成员、不得写 change、不得刷 last_seen_on。"""
    members_61 = [{"symbol": f"60{i:04d}", "name": f"股{i}"} for i in range(61)]
    repo = make_fake_repo(stock_ids={m["symbol"]: i for i, m in enumerate(members_61)})
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(
            boards=[
                {"board_code": "BK0001", "board_name": "A", "member_total": 63},
                {"board_code": "BK0002", "board_name": "B", "member_total": 1},
            ],
            members={"BK0001": members_61, "BK0002": [{"symbol": "600001", "name": "邯郸钢铁"}]},
        ),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["partial_boards"] == 1 and out["failed_boards"] == 0
    assert out["added"] == 1 and out["boards"] == 2
    assert [code for code, _ in repo.member_calls] == ["BK0002"], "部分页板不得进入写入路径"
    assert [c["board_code"] for c in repo.changes] == ["BK0002"]
    assert "BK0001" not in repo.members


async def test_ingest_accepts_exact_total_and_rejects_short_page(
    monkeypatch: pytest.MonkeyPatch, make_fake_repo: Any
) -> None:
    """门禁边界：len == member_total 视为完整照常入库；只对 len < total 判 partial。"""
    repo = make_fake_repo()
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(
            boards=[{"board_code": "BK0001", "board_name": "A", "member_total": 2}],
            members={
                "BK0001": [
                    {"symbol": "600000", "name": "浦发银行"},
                    {"symbol": "600001", "name": "邯郸钢铁"},
                ]
            },
        ),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["partial_boards"] == 0 and out["added"] == 2
    assert [code for code, _ in repo.member_calls] == ["BK0001"]


async def test_ingest_member_total_none_ingests_but_counts_partial(
    monkeypatch: pytest.MonkeyPatch, fake_repo: _FakeRepo
) -> None:
    """`member_total is None`（东财三项计数全不可解析）无法对账：仍入库但进 partial 上报。"""
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(boards=[{"board_code": "BK0001", "board_name": "A", "member_total": None}]),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["partial_boards"] == 1 and out["failed_boards"] == 0
    assert out["added"] == 2, "无法判完整性不等于跳过：仍按客户端护栏结果入库"
    assert [c["board_code"] for c in fake_repo.changes] == ["BK0001", "BK0001"]


async def test_ingest_counts_unresolved_members_and_maps_once(
    monkeypatch: pytest.MonkeyPatch, make_fake_repo: Any
) -> None:
    """名录未收录 (=) 的成员 stock_id 留 NULL 并计入 unresolved；全 run 只查一次映射。"""
    repo = make_fake_repo(stock_ids={"600000": 7})
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(
            boards=[
                {"board_code": "BK0001", "board_name": "A", "member_total": 1},
                {"board_code": "BK0002", "board_name": "B", "member_total": 1},
            ],
            members={
                "BK0001": [{"symbol": "600000", "name": "浦发银行"}],
                "BK0002": [{"symbol": "999999", "name": "未收录"}],
            },
        ),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["unresolved"] == 1 and out["added"] == 2
    assert len(repo.mapped_symbols) == 1, "symbol → stock_id 必须全 run 只查一次"
    assert sorted(repo.mapped_symbols[0]) == ["600000", "999999"]
    by_board = {code: rows for code, rows in repo.member_calls}
    assert by_board["BK0001"][0]["stock_id"] == 7
    assert by_board["BK0002"][0]["stock_id"] is None, "未收录不得臆造 stock_id"


async def test_ingest_partial_board_members_do_not_inflate_unresolved(
    monkeypatch: pytest.MonkeyPatch, make_fake_repo: Any
) -> None:
    """partial 板未写入，其成员不得计入 unresolved（否则门禁拦住的行会把指标虚高）。"""
    repo = make_fake_repo()
    monkeypatch.setattr(
        concept_service,
        "_get_eastmoney",
        lambda: _FakeEm(
            boards=[
                {"board_code": "BK0001", "board_name": "A", "member_total": 3},
                {"board_code": "BK0002", "board_name": "B", "member_total": 1},
            ],
            members={
                "BK0001": [
                    {"symbol": "600000", "name": "浦发银行"},
                    {"symbol": "600001", "name": "邯郸钢铁"},
                ],
                "BK0002": [{"symbol": "600002", "name": "东北证券"}],
            },
        ),
    )
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert out["partial_boards"] == 1 and out["unresolved"] == 1
    assert sorted(repo.mapped_symbols[0]) == ["600002"], "只映射真正会入库的成员"


async def test_ingest_board_list_failure_raises_and_never_deactivates(
    monkeypatch: pytest.MonkeyPatch, fake_repo: _FakeRepo
) -> None:
    """板块列表抓取失败：直接抛（T5 记录），绝不带空集合走 deactivate → 不会全量下架。"""
    monkeypatch.setattr(concept_service, "_get_eastmoney", lambda: _FakeEm(fail_board_list=True))
    with pytest.raises(httpx.TransportError):
        await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert fake_repo.deactivate_calls == [], "列表失败不得调用 deactivate_missing_boards"
    assert fake_repo.member_calls == [] and fake_repo.boards_rows == []


async def test_ingest_upserts_boards_first_and_deactivates_last(
    monkeypatch: pytest.MonkeyPatch, fake_repo: _FakeRepo
) -> None:
    """写入顺序契约：名录 upsert → 逐板成分 → 最后按本轮在册 code 停用缺失板。"""
    monkeypatch.setattr(concept_service, "_get_eastmoney", lambda: _FakeEm(fail_on="BK0002"))
    out = await concept_service.ingest_concept_members(db=None)  # type: ignore[arg-type]
    assert fake_repo.boards_rows == _DEFAULT_BOARDS, "名单原样交给 upsert_boards（含 member_total）"
    assert fake_repo.events == ["boards", "members:BK0001", "deactivate"]
    assert fake_repo.deactivate_calls == [{"BK0001", "BK0002"}], (
        "停用集合取列表全集（失败板仍在册），不得只传成功板"
    )
    assert out["members_upserted"] == 2, "members_upserted = added + updated"
