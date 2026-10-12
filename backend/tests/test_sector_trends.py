"""Task 6b：`GET /market/sector-trends`（读时计算，不物化）+ 完整日闸门。

覆盖点（按 brief 与 controller rulings）：

1. **`eod_full` 完整日闸门**（Ruling 21）：窗口内**只有 `poll` 行**的日子不得进序列，
   必须落进 `missing_days`；`available_from` = 该维度最早的 `eod_full` 日（可为 None：
   表里根本没有完整日时**不得**假装有）。
2. **缺日 ≠ 0 流入**（Ruling 7）：缺日不进任何聚合，`missing_days` 里的日子既不算
   "净流入 0"也不打断/续接连击 —— 只在请求窗口与完整日**交集**上统计。
3. **归属显式**（Ruling 22）：`industry`/`region` → `attribution="current_sw_snapshot"`
   （UI 文案"按当前分类回溯，非当时成分"）；`concept` → `attribution="point_in_time"`
   （成分由 `concept_members` 当前态 + `concept_member_changes` **反推**时点集合，
   绝不用当前成分冒充历史成分）。
4. **`top_n` 显式**（Ruling 6）：默认 20、有界；上榜榜序 `main_net_inflow DESC,
   board_code ASC`（确定性 tiebreak）；`连续上榜天数` 在**完整日**上计算。
5. **窗口守恒**：`days` 缺省 60 / 上限 120；`series` 升序；缓存键含
   `dimension`/`days`/`top_n`；空结果不缓存。

**离线**：默认门禁用例全部打桩（repo 替身 / 假 session），不碰 PG、Redis、网络。
真库用例标 ``e2e``（哨兵日 1999-01-04 自建自清，且**不碰任何真实交易日**）。
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import get_cache
from app.main import app
from app.repositories import market_data_repo
from app.services import market_data_service as mds

# 1999-01-04 是**哨兵日**：早于任何真实快照（顺带不改变 `max(trade_date)`，
# 于是不影响 `/market/sector-moneyflow` 的"最近日"语义），真库用例自建自清。
SENTINEL = date(1999, 1, 4)
SENTINEL_2 = date(1999, 1, 5)
# 工作窗口（与真实历史日期无关，桩数据自洽即可）
D1, D2, D3, D4 = date(2026, 9, 28), date(2026, 9, 29), date(2026, 9, 30), date(2026, 10, 9)


def _snap(
    day: date,
    code: str,
    *,
    name: str | None = None,
    inflow: float | None = 1.0e9,
    pct: float | None = 1.0,
    up: int | None = 10,
    down: int | None = 3,
    capture_kind: str = market_data_repo.EOD_FULL_CAPTURE,
    dimension: str = "industry",
) -> SimpleNamespace:
    """一行 ``sector_moneyflow_snapshots``（ORM 行替身：直读属性）。"""
    return SimpleNamespace(
        trade_date=day,
        dimension=dimension,
        board_code=code,
        board_name=name or f"板块{code}",
        pct_change=pct,
        main_net_inflow=inflow,
        super_large_net=None,
        large_net=None,
        main_net_ratio=None,
        up_count=up,
        down_count=down,
        lead_stock_name="龙头",
        lead_stock_code="600000",
        lead_stock_pct=1.0,
        amount=None,
        flat_count=1,
        capture_kind=capture_kind,
    )


class _RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.gets: list[str] = []
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))
        self.store[key] = value


def _days(offsets: list[date]) -> list[date]:
    return list(offsets)


# ---------------------------------------------------------------------------
# 1. 完整日闸门 + 缺日语义（纯函数，不触 DB）
# ---------------------------------------------------------------------------


def test_available_from_is_earliest_eod_full_day_not_earliest_row() -> None:
    """`available_from` = 最早**完整日**；一个完整日都没有 → `None`（不假装）。"""
    assert mds.resolve_available_from([D1, D2], [D1, D2, D3]) == D1
    assert mds.resolve_available_from([], [D1, D2]) is None


def test_missing_days_lists_poll_only_days_and_excludes_them_from_samples() -> None:
    """`poll`-only 日必须落进 `missing_days`，且**不得**出现在序列的 samples 里。"""
    trading_days = [D1, D2, D3]
    rows = [
        _snap(D1, "BK0001"),  # eod_full
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),  # 只有 poll
        # D3 完全无行
    ]
    complete = mds.eod_full_days_from_rows(rows)
    assert complete == {D1}
    assert mds.missing_eod_days(trading_days, complete) == [D2.isoformat(), D3.isoformat()]
    assert mds.intersect_complete(trading_days, complete) == [D1]


def test_missing_day_is_not_zero_inflow() -> None:
    """缺日**不进**聚合：D2 缺 → 不产生 `main_net_inflow=0` 的伪行，连击也不被它打断。

    构造：D1/D3 有完整日（BK0001 连续两日净流入），D2 只有 poll 行（缺）。
    正确口径下 BK0001 = 2 天净流入连击（D1→D3 在**完整日序列**上相邻）；
    "缺日当 0"会把它算成 0 连击（或 1 天）。
    """
    rows = [
        _snap(D1, "BK0001", inflow=2.0e9),
        _snap(D2, "BK0001", inflow=-9.0e9, capture_kind=market_data_repo.POLL_CAPTURE),
        _snap(D3, "BK0001", inflow=1.0e9),
    ]
    complete_days = [D1, D3]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=complete_days,
        all_complete_days=[D1, D3],
        trading_days=[D1, D2, D3],
        dimension="industry",
        days=60,
        top_n=20,
    )

    assert payload["available_from"] == D1
    assert payload["window"]["samples"] == 2
    # D2 只有 poll 行 → 缺完整日；D1/D3 都在 complete_days 里 → 不缺
    assert payload["missing_days"] == [D2.isoformat()]
    board = payload["boards"][0]
    assert board["board_code"] == "BK0001"
    assert board["main_net_inflow"] == 1.0e9  # **最后完整日**的值，不是缺日的 0
    assert board["streak_inflow"] == 2
    assert board["streak_outflow"] == 0
    assert [p["trade_date"] for p in board["series"]] == [D1, D3]


def test_top_n_bounded_and_ranking_includes_only_complete_days() -> None:
    """上榜榜序 = `main_net_inflow DESC, board_code ASC`；`top_n` 之外的板连击为 0。"""
    rows = [
        _snap(D1, "BK0003", inflow=3.0e9),
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D1, "BK0002", inflow=5.0e9),  # 与 BK0001 同额 → 板码升序定序
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1],
        all_complete_days=[D1],
        trading_days=[D1],
        dimension="industry",
        days=60,
        top_n=2,
    )
    ranked = mds.rank_boards(rows, D1, top_n=2)
    assert [r.board_code for r in ranked] == ["BK0001", "BK0002"]
    assert payload["top_n"] == 2
    stats = {b["board_code"]: b for b in payload["boards"]}
    assert stats["BK0001"]["streak_on_top_n"] == 1
    assert stats["BK0002"]["streak_on_top_n"] == 1
    assert stats["BK0003"]["streak_on_top_n"] == 0  # 只有 1 天且不在 Top-2 → 未上榜


def test_leader_change_count_and_attribution_fields() -> None:
    """龙头更替次数 = 完整日相邻两天榜首变化次数；`attribution` 必填且随维度变化。"""
    rows = [
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D1, "BK0002", inflow=1.0e9),
        _snap(D2, "BK0001", inflow=1.0e9),
        _snap(D2, "BK0002", inflow=5.0e9),
        _snap(D3, "BK0002", inflow=6.0e9),
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2, D3],
        all_complete_days=[D1, D2, D3],
        trading_days=[D1, D2, D3],
        dimension="industry",
        days=60,
        top_n=20,
    )
    # D1 龙头 BK0001 → D2 BK0002（1 次）→ D3 仍 BK0002（0 次）
    assert payload["leader_change_count"] == 1
    assert payload["leader_changes"] == [
        {"trade_date": D2, "board_code": "BK0002", "board_name": "板块BK0002"}
    ]
    assert payload["attribution"] == "current_sw_snapshot"

    concept = mds.sector_trends_payload(
        rows=[_snap(D1, "BK0896", dimension="concept")],
        complete_days=[D1],
        all_complete_days=[D1],
        trading_days=[D1],
        dimension="concept",
        days=60,
        top_n=20,
    )
    assert concept["attribution"] == "point_in_time"


def test_point_in_time_concept_membership_reconstruction() -> None:
    """概念时点成分**反推**：当前态 - 之后的 add + 之后的 remove。

    构造（BK0896）：S1 从 09-20 起在板；S2 于 09-22 被 **add**；S3 于 09-22 被 **remove**。
    - 09-21 时点成分 = {S1, S3}
    - 09-22 时点成分 = {S1, S2}
    用当前成分（{S1,S2}）冒充 09-21 就会把 S2 的涨停算进 09-21。
    """
    current = {"BK0896": {"S1", "S2"}}
    d20, d21, d22 = date(2026, 9, 20), date(2026, 9, 21), date(2026, 9, 22)
    changes = [
        {"observed_on": d20, "board_code": "BK0896", "symbol": "S1", "change_type": "add"},
        {"observed_on": d22, "board_code": "BK0896", "symbol": "S2", "change_type": "add"},
        {"observed_on": d22, "board_code": "BK0896", "symbol": "S3", "change_type": "remove"},
    ]
    sets = mds.concept_membership_snapshots(current, changes, on_days=[d21, d22])
    assert sets[d21] == {"BK0896": {"S1", "S3"}}
    assert sets[d22] == {"BK0896": {"S1", "S2"}}


def test_concept_lu_count_uses_point_in_time_membership() -> None:
    """板块涨停家数按**时点成分**归属：09-21 的 S2 涨停不计入（当时 S2 还不在板）。"""
    facts = [
        {"trade_date": date(2026, 9, 21), "symbol": "S1"},
        {"trade_date": date(2026, 9, 21), "symbol": "S2"},
        {"trade_date": date(2026, 9, 22), "symbol": "S2"},
    ]
    membership = {
        date(2026, 9, 21): {"BK0896": {"S1", "S3"}},
        date(2026, 9, 22): {"BK0896": {"S1", "S2"}},
    }
    counts = mds.limit_up_counts_by_board(facts, membership)
    assert counts[(date(2026, 9, 21), "BK0896")] == 1  # 只有 S1
    assert counts[(date(2026, 9, 22), "BK0896")] == 1  # 只有 S2（S1 当日未涨停）


def test_no_eod_full_day_means_no_available_from_and_never_zero_inflow() -> None:
    """表里没有任何 `eod_full` 行 → `available_from=None`、序列空、全窗口缺日。

    **不得**把 poll 行当成有数据、更不得给 `main_net_inflow=0` 的伪行（当前真库正是
    这个状态：15:05 EOD job 上线前 `capture_kind='eod_full'` 行数为 0）。
    """
    rows = [
        _snap(D1, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE),
    ]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[],
        all_complete_days=[],
        trading_days=[D1, D2],
        dimension="industry",
        days=60,
        top_n=20,
    )
    assert payload["available_from"] is None
    assert payload["boards"] == []
    assert payload["window"]["samples"] == 0
    assert payload["window"]["from"] is None
    assert payload["window"]["to"] is None
    assert payload["as_of"] is None
    assert payload["missing_days"] == [D1.isoformat(), D2.isoformat()]


async def test_empty_result_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """空结果不得进缓存（`boards=[]` 不能被冻结成 300s 的"暂无数据"）。"""

    class _Factory:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def __call__(self) -> _Factory:
            return self

    async def _empty(*a: Any, **k: Any) -> list[Any]:
        return []

    async def _none(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(mds, "async_session_factory", _Factory(), raising=False)
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow_trading_days", _empty)
    monkeypatch.setattr(market_data_repo, "list_trading_days", _empty)
    monkeypatch.setattr(market_data_repo, "earliest_sector_moneyflow_complete_day", _none)
    import app.core.database as _db

    monkeypatch.setattr(_db, "async_session_factory", _Factory())

    cache = _RecordingCache()
    payload = await mds.get_sector_trends(cache, dimension="industry", days=60, top_n=20)

    assert payload["boards"] == []
    assert payload["available_from"] is None
    assert cache.sets == []


# ---------------------------------------------------------------------------
# 2. repo 查询：完整日谓词 / 区间过滤
# ---------------------------------------------------------------------------


async def test_list_sector_moneyflow_complete_days_filters_capture_kind() -> None:
    """完整日行的 WHERE 必须含 `capture_kind='eod_full'` 与维度（Ruling 21 的 SQL 面）。"""
    from sqlalchemy.dialects import postgresql

    captured: list[Any] = []

    class _Db:
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            captured.append(stmt)
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    await market_data_repo.list_sector_moneyflow_complete_days(
        _Db(),
        "industry",
        D1,
        D4,  # type: ignore[arg-type]
    )
    compiled = captured[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "capture_kind" in sql
    assert "ORDER BY" in sql.upper()
    # `eod_full` 是**绑定参数**（不是内联字面量）：断言参数值，别只 grep SQL 文本
    assert market_data_repo.EOD_FULL_CAPTURE in compiled.params.values(), compiled.params


async def test_list_concept_membership_changes_is_ascending_by_observed_on() -> None:
    captured: list[Any] = []

    class _Db:
        async def execute(self, stmt: Any, params: Any = None) -> Any:
            captured.append(stmt)
            return SimpleNamespace(all=lambda: [])

    await market_data_repo.list_concept_membership_changes(_Db(), D1, D4)  # type: ignore[arg-type]
    from sqlalchemy.dialects import postgresql

    sql = str(captured[0].compile(dialect=postgresql.dialect()))
    assert "concept_member_changes" in sql
    assert "ASC" in sql.upper()


# ---------------------------------------------------------------------------
# 3. 端点级：200 + 形状 + 契约（与 /market/sector-moneyflow 同日同值）
# ---------------------------------------------------------------------------


@pytest.fixture
async def _api_cache() -> Any:

    cache = _RecordingCache()

    async def _cache_dep() -> Any:
        return cache

    app.dependency_overrides[get_cache] = _cache_dep
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, cache
    finally:
        app.dependency_overrides.clear()


def _patch_sector_db(
    monkeypatch: pytest.MonkeyPatch, rows: list[SimpleNamespace], trading: list[date]
) -> None:
    """把 `get_sector_trends` 的读路径换成桩（service 内联 import 的是模块属性）。"""

    async def _market_days(
        db: Any, start: date, end: date, *, limit: int | None = None
    ) -> list[date]:
        window = [d for d in trading if start <= d <= end]
        return window[-limit:] if limit is not None else window

    async def _snapshot_days(db: Any, dimension: str, *, limit: int) -> list[date]:
        days = sorted({r.trade_date for r in rows if r.dimension == dimension})
        return days[-limit:]

    async def _earliest_complete(db: Any, dimension: str, **kw: Any) -> date | None:
        days = [
            r.trade_date
            for r in rows
            if r.dimension == dimension and r.capture_kind == market_data_repo.EOD_FULL_CAPTURE
        ]
        return min(days) if days else None

    async def _range(db: Any, dimension: str, start: date, end: date, **kw: Any) -> list[Any]:
        return [r for r in rows if r.dimension == dimension and start <= r.trade_date <= end]

    class _Factory:
        async def __aenter__(self) -> Any:
            return object()

        async def __aexit__(self, *exc: Any) -> None:
            return None

        def __call__(self) -> _Factory:
            return self

    monkeypatch.setattr(market_data_repo, "list_trading_days", _market_days)
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow_trading_days", _snapshot_days)
    monkeypatch.setattr(
        market_data_repo, "earliest_sector_moneyflow_complete_day", _earliest_complete
    )
    monkeypatch.setattr(market_data_repo, "list_sector_moneyflow_range", _range)
    monkeypatch.setattr(mds, "async_session_factory", _Factory(), raising=False)
    import app.core.database as _db

    monkeypatch.setattr(_db, "async_session_factory", _Factory())


async def test_sector_trends_endpoint_shape(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any
) -> None:
    """端点 200；`available_from`/`attribution`/`top_n`/`missing_days` 齐备；天数越界 422。"""
    client, cache = _api_cache
    rows = [
        _snap(D1, "BK0001", inflow=5.0e9),
        _snap(D2, "BK0001", capture_kind=market_data_repo.POLL_CAPTURE, inflow=1.0e9),
        _snap(D3, "BK0001", inflow=2.0e9),
    ]
    _patch_sector_db(monkeypatch, rows, [D1, D2, D3])

    async def _no_concept(db: Any, start: date, end: date) -> list[Any]:
        return []

    monkeypatch.setattr(market_data_repo, "list_concept_membership_changes", _no_concept)
    monkeypatch.setattr(market_data_repo, "list_concept_members_current", _no_concept)

    resp = await client.get("/api/v1/market/sector-trends", params={"dimension": "industry"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["dimension"] == "industry"
    assert body["available_from"] == D1.isoformat()
    assert body["attribution"] == "current_sw_snapshot"
    assert body["attribution_note"] == "按当前分类回溯，非当时成分"
    assert body["lu_count_source"] == "unavailable"
    assert body["top_n"] == 20
    # 窗口 = 表内实际持有的交易日（桩里 3 天），不是请求上限 60
    assert body["window"]["days"] == 3
    assert body["missing_days"] == [D2.isoformat()]
    assert [b["board_code"] for b in body["boards"]] == ["BK0001"]
    assert body["as_of"] == D3.isoformat()
    assert any(k.endswith(":industry:60:20") for k in cache.gets), cache.gets

    resp_over = await client.get(
        "/api/v1/market/sector-trends", params={"dimension": "industry", "days": 121}
    )
    assert resp_over.status_code == 422
    assert (
        await client.get("/api/v1/market/sector-trends", params={"dimension": "nope"})
    ).status_code == 422
    assert (
        await client.get(
            "/api/v1/market/sector-trends", params={"dimension": "industry", "top_n": 0}
        )
    ).status_code == 422


@pytest.mark.parametrize("dimension", ["industry", "concept", "region"])
async def test_sector_trends_cache_key_includes_every_dimension(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any, dimension: str
) -> None:
    """缓存键必须含 `dimension` + `days` + `top_n`（漏一个就会串味）。"""
    client, cache = _api_cache
    rows = [_snap(D1, "BK0001", dimension=dimension)]
    _patch_sector_db(monkeypatch, rows, [D1])

    async def _empty(db: Any, *a: Any, **k: Any) -> list[Any]:
        return []

    monkeypatch.setattr(market_data_repo, "list_concept_membership_changes", _empty)
    monkeypatch.setattr(market_data_repo, "list_concept_members_current", _empty)

    resp = await client.get(
        "/api/v1/market/sector-trends",
        params={"dimension": dimension, "days": 42, "top_n": 7},
    )
    assert resp.status_code == 200, resp.text
    assert any(f":{dimension}:42:7" in k for k in cache.gets), cache.gets


# ---------------------------------------------------------------------------
# 4. 真库（`-m e2e`）：哨兵日自建自清 + 与 `/market/sector-moneyflow` 同日同值
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_sector_trends_real_db_gate_and_consistency() -> None:
    """真库：完整日闸门如实 + 最后完整日的板值与 `/market/sector-moneyflow` 同源同值。

    - 真库当前（T2 的 15:05 EOD job 上线前）`capture_kind='eod_full'` 行数为 **0**
      → `available_from=None`、`window.samples=0`、`boards=[]`（**绝不**用 poll 行冒充、
      绝不把缺日算成 0 流入）。
    - 再往**该维度表内最近的交易日**（= `/market/sector-moneyflow` 的 `as_of` 日）写一行
      `board_code='BK9999'` 的 `eod_full` 哨兵行，自建自清：该日成为 `available_from`
      与"最后完整日"，且该行的值与 `get_sector_moneyflow(cache=None)` 返回的**同板**行
      逐字相等（同源、同映射）。哨兵板码不与任何真实板块冲突，清理只删自己那一行。
    """
    from sqlalchemy import text

    from app.core.database import async_session_factory, engine

    cache = _RecordingCache()
    sentinel_rows = [
        {
            "board_code": "BK9999",
            "board_name": "哨兵板块",
            "pct_change": 2.5,
            "main_net_inflow": 1.5e9,
            "up_count": 12,
            "down_count": 3,
            "flat_count": 1,
            "amount": None,
            "super_large_net": None,
            "large_net": None,
            "main_net_ratio": None,
            "lead_stock_name": None,
            "lead_stock_code": None,
            "lead_stock_pct": None,
        }
    ]

    async def _cleanup() -> None:
        async with async_session_factory() as db:
            await db.execute(
                text(
                    "DELETE FROM sector_moneyflow_snapshots "
                    "WHERE board_code = 'BK9999' AND board_name = '哨兵板块'"
                )
            )
            await db.commit()

    # 上一批 `engine.dispose()` 已释放其事件循环上的连接池；本用例自己的循环里再 dispose
    # 一次（幂等）确保没有跨循环连接被复用（全量 suite 实测过 `Event loop is closed`）。
    await engine.dispose()
    try:
        await _cleanup()

        # 闸门如实：真库没有任何 eod_full 行 → 无 available_from、无样本、无板块行
        honest = await mds.get_sector_trends(cache, dimension="industry", days=60)
        assert honest["available_from"] is None, honest
        assert honest["window"]["samples"] == 0
        assert honest["window"]["from"] is None and honest["window"]["to"] is None
        assert honest["boards"] == []
        assert honest["leader_change_count"] == 0 and honest["leader_changes"] == []

        # 哨兵：写在该维度**窗口内最早**的交易日（真库里该日只有 poll 行，哨兵板码
        # BK9999 不与任何真实板块冲突），自建 → 实测 → 自清。
        async with async_session_factory() as db:
            days_in_window = await market_data_repo.list_sector_moneyflow_trading_days(
                db, "industry", limit=60
            )
            trading = await market_data_repo.list_trading_days(
                db, days_in_window[0], days_in_window[-1]
            )
            day = trading[0]
            reference_before = await mds.get_sector_moneyflow(None, "industry", 100)  # type: ignore[arg-type]
            assert reference_before["as_of"] is not None
            await market_data_repo.upsert_sector_moneyflow(
                db,
                day,
                "industry",
                sentinel_rows,
                capture_kind=market_data_repo.EOD_FULL_CAPTURE,
            )
            await db.commit()

        payload = await mds.get_sector_trends(cache, dimension="industry", days=60)
        assert payload["available_from"] == day, payload["available_from"]
        assert payload["as_of"] == day
        assert payload["window"]["samples"] == 1
        assert payload["window"]["from"] == day and payload["window"]["to"] == day
        assert payload["attribution"] == "current_sw_snapshot"
        assert payload["attribution_note"] == "按当前分类回溯，非当时成分"
        assert payload["leader_change_count"] == 0  # 只有一个完整日 → 没有"更替"
        sentinel_board = next(b for b in payload["boards"] if b["board_code"] == "BK9999")
        assert sentinel_board["main_net_inflow"] == 1.5e9
        assert sentinel_board["pct_change"] == 2.5
        assert sentinel_board["up_count"] == 12
        assert sentinel_board["streak_on_top_n"] == 1
        assert sentinel_board["streak_inflow"] == 1
        # 只有哨兵那天是完整日 → 窗口内其余交易日全部点名（缺日 ≠ 0 流入）
        assert day.isoformat() not in payload["missing_days"]
        assert len(payload["missing_days"]) == payload["window"]["days"] - 1

        # 同源一致性①：`/market/sector-moneyflow` 这条生产读路径读的就是表里那一行
        envelope = await mds.get_sector_moneyflow(None, "industry", 100)  # type: ignore[arg-type]
        assert envelope["as_of"] is not None
        async with async_session_factory() as db:
            physical = {
                r.board_code: r
                for r in await market_data_repo.list_sector_moneyflow(
                    db, date.fromisoformat(str(envelope["as_of"])), "industry", 100
                )
            }
        latest = envelope["items"][0]
        assert latest["main_net_inflow"] == physical[latest["board_code"]].main_net_inflow
        assert latest["pct_change"] == physical[latest["board_code"]].pct_change
        assert latest["up_count"] == physical[latest["board_code"]].up_count
        assert latest["down_count"] == physical[latest["board_code"]].down_count

        # 同源一致性②：6b 的哨兵行与它写在表里的那一行（`list_sector_moneyflow`
        # 的生产读路径）逐字相等 —— 两条路径读的是同一行，不是两份口径
        async with async_session_factory() as db:
            stored = {
                r.board_code: r
                for r in await market_data_repo.list_sector_moneyflow(db, day, "industry", 100)
            }["BK9999"]
        assert sentinel_board["main_net_inflow"] == stored.main_net_inflow
        assert sentinel_board["pct_change"] == stored.pct_change
        assert sentinel_board["up_count"] == stored.up_count
        assert sentinel_board["down_count"] == stored.down_count
    finally:
        await _cleanup()
        await engine.dispose()


def test_lu_count_is_rendered_only_where_point_in_time_membership_exists() -> None:
    """`lu_count` 进序列只在有时点成分的日子；没有成分的日子是 `None`（不是 0）。

    0 是"当日该板一只涨停都没有"这一**事实断言**；`None` 是"这天算不出来"。
    两者混用会把"还没积累成分差分"的历史日显示成"零涨停"。
    """
    rows = [_snap(D1, "BK0896", dimension="concept"), _snap(D2, "BK0896", dimension="concept")]
    payload = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2],
        all_complete_days=[D1, D2],
        trading_days=[D1, D2],
        dimension="concept",
        days=60,
        top_n=20,
        lu_counts={(D1, "BK0896"): 3},
    )
    board = next(b for b in payload["boards"] if b["board_code"] == "BK0896")
    assert [p["lu_count"] for p in board["series"]] == [3, None]
    assert payload["lu_count_source"] == "point_in_time_membership"

    industry = mds.sector_trends_payload(
        rows=rows,
        complete_days=[D1, D2],
        all_complete_days=[D1, D2],
        trading_days=[D1, D2],
        dimension="industry",
        days=60,
        top_n=20,
    )
    assert all(p["lu_count"] is None for b in industry["boards"] for p in b["series"])
    assert industry["lu_count_source"] == "unavailable"
