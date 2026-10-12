"""Task 2：板块快照——`/market/hot-boards` 改读当日表 + 补 2 列 + 当日 EOD/补写。

覆盖点（按 brief 与 controller rulings）：

1. ``fetch_sector_moneyflow`` 的 ``fields`` 是**超集**（含 ``f6`` 成交额 / ``f106`` 平盘家数）
   且**只发一次** clist 请求 —— 不新增第二次 ``fetch_board_list``，也不丢既有
   ``f66``/``f72``（丢了会回归 ``/market/sector-moneyflow`` 卡片）。
2. 快照表 3 列（``amount Numeric(20,4)`` / ``flat_count Integer`` / ``capture_kind``）：
   upsert 写它们；上游缺值恒 NULL，**绝不填 0**；``eod_full`` 不被后续 poll 降级。
2b. **15:05 EOD 分页全量**（Ruling 11）：复用 ``_paged_clist`` 翻页直到覆盖上游 ``data.total``
   （``clist`` 的 ``pz`` 上限 100），行数不足要 WARNING + `job:failures`（不许静默截断）；
   同一个 mapper 与单次 poll 路径共用（口径不分叉）。
3. 读表闸门（Ruling 11）：表内该维度的最近日 == 最近**预期**交易日，**且**该
   ``(day, dimension)`` 存在 ``capture_kind='eod_full'``（15:05 分页全量捕获）行时才服务
   表行；否则回落实时抓取（**绝不返回空表**）。读表日若与今天不同，``as_of`` 就报那一日 ——
   不把陈旧日改标成今天。盘中只有 ``f62`` Top-100 的 poll 行，**不得**冒充 f3 全量榜。
4. 读表 items 的字段集 ⊇ 前端 ``HotBoardItem``（直接解析
   ``frontend/src/shared/api/market.ts``，不是抄一份字面量：前端加字段而这里不补 → 红）。
5. 读表行序 ``pct_change DESC, board_code ASC``（确定性 tiebreak；SQL 侧守卫 + repo 契约）。
6. 当日 EOD job（15:05 全量）/ 缺 ``eod_full`` 才补写的 job（15:35）+ 双轨制（worker 与 job
   调同一 service 方法）；覆盖不足两条轨道都要告警；cron 显式 ``Asia/Shanghai``；**不新增队列**。
7. A 类边界：东财无历史接口 → 本文件（与实现）不含任何"补昨天"的路径，缺行只回落实时。

**离线**：默认门禁用例全部打桩（东财 ``_get_json`` 夹具 / 读表 seam / 判据日），不碰
PG、Redis、网络。真库用例标 ``e2e``（默认 deselect），自建自清一个 1999 年的 scratch 日
—— 不碰任何真实交易日（并发运行的 scheduler 正在写今天）。
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, get_args
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy.dialects import postgresql

from app.repositories import market_data_repo
from app.schemas.task import MarketDataJobType
from app.services import market_data_service as mds
from app.services import market_service

D_TODAY = date(2026, 9, 18)  # 周五：`_today_sh` 的桩值（= 最近预期交易日）
D_SAT = date(2026, 9, 19)  # 周六：预期交易日应回落到 09-18
D_PREV = date(2026, 9, 17)
D_ANCIENT = date(1999, 1, 4)  # 真库 scratch 日：早于任何真实快照，且不影响 max(trade_date)
#: 收盘守卫（评审 Important #1）的注入时刻：上海时区
_SH_TZ = ZoneInfo("Asia/Shanghai")
D_BEFORE_CLOSE = datetime(2026, 9, 18, 14, 30, tzinfo=_SH_TZ)  # 盘中
D_AT_CLOSE = datetime(2026, 9, 18, 15, 0, tzinfo=_SH_TZ)  # 边界：含
D_AFTER_CLOSE = datetime(2026, 9, 18, 15, 5, tzinfo=_SH_TZ)  # 15:05 job 的真实时刻

_FRONTEND_MARKET_TS = (
    Path(__file__).resolve().parents[2] / "frontend" / "src" / "shared" / "api" / "market.ts"
)


def _ts_interface_fields(name: str) -> set[str]:
    """从 ``frontend/src/shared/api/market.ts`` 解析出接口的字段名集合。

    机械地钉住"后端 items ⊇ 前端契约"：前端给 ``HotBoardItem`` 加一个字段，而后端读表
    分支不供它 —— 这个测试立刻红（比在 Python 里抄一份字面量强，抄的那份不会跟着漂）。
    """
    source = _FRONTEND_MARKET_TS.read_text(encoding="utf-8")
    body = re.search(rf"export interface {name} \{{(.*?)\n\}}", source, re.S)
    assert body is not None, f"{name} not found in {_FRONTEND_MARKET_TS}"
    fields = set(re.findall(r"^\s{2}(\w+)\??:", body.group(1), re.M))
    assert fields, f"{name} parsed to zero fields"
    return fields


# ---------------------------------------------------------------------------
# 替身
# ---------------------------------------------------------------------------


def _async_return(value: Any) -> Any:
    async def _f(*_a: Any, **_k: Any) -> Any:
        return value

    return _f


def _boom_factory(message: str) -> Any:
    """同步替身：一被调用就 AssertionError（用于钉"这条路径根本不该被走到"）。"""

    def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(message)

    return _boom


def _raise_factory(message: str) -> Any:
    """异步替身（同上，供 await 的调用点用）。"""

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise AssertionError(message)

    return _boom


class _CursorResult:
    """``CursorResult`` 的最小替身（``upsert_*`` 读 ``rowcount`` 判真实写入行数）。"""

    rowcount = 0


class _RecordingDb:
    """只记录语句的 session 替身（不执行、不连库）。"""

    def __init__(self, result: Any = None) -> None:
        self.statements: list[Any] = []
        self._result = result if result is not None else _CursorResult()

    async def execute(self, stmt: Any) -> Any:
        self.statements.append(stmt)
        return self._result

    async def flush(self) -> None:
        return None


class _EmptyResult:
    def scalars(self) -> _EmptyResult:
        return self

    def all(self) -> list[Any]:
        return []

    def scalar(self) -> None:
        return None

    def first(self) -> None:
        return None


class _PagedEM:
    """按 ``pn`` 返回预置页的 ``_get_json`` 替身（翻页用）；记录每次请求参数。"""

    def __init__(self, pages: dict[int, dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, base: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(params)
        return self.pages[params["pn"]]


def _raw_flow_row(code: str, *, pct: float = 1.0) -> dict[str, Any]:
    """一页里的东财原始行（含全部 fields，供翻页替身用）。"""
    return {
        "f12": code,
        "f14": f"板块{code}",
        "f3": pct,
        "f6": 1000.0,
        "f62": 200.0,
        "f66": 100.0,
        "f72": 100.0,
        "f104": 10,
        "f105": 5,
        "f106": 2,
        "f184": 3.0,
        "f128": "龙头",
        "f136": 1.0,
        "f140": "600000",
    }


def _snap(**overrides: Any) -> Any:
    """一行 ``sector_moneyflow_snapshots``（含 2 新列；``amount`` 是 Numeric 的 Decimal）。"""
    fields: dict[str, Any] = {
        "board_code": "BK1211",
        "board_name": "汽车",
        "pct_change": 1.35,
        "main_net_inflow": 3.7e9,
        "super_large_net": 2.7e9,
        "large_net": 9.3e8,
        "main_net_ratio": 6.46,
        "up_count": 235,
        "down_count": 87,
        "lead_stock_name": "比亚迪",
        "lead_stock_code": "002594",
        "lead_stock_pct": 3.1,
        "amount": Decimal("123456.7890"),
        "flat_count": 11,
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def _hot_board_row(**overrides: Any) -> dict[str, Any]:
    """快照行 → hot-boards 行 dict（走真实 mapper）。"""
    return mds._hot_board_snapshot_row(_snap(**overrides))


class _StaticEM:
    """``fetch_board_list`` 返回预置归一行的客户端替身；记录每次 category。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls: list[str] = []

    async def fetch_board_list(self, category: str) -> list[dict[str, Any]]:
        self.calls.append(category)
        return self.rows


def _live_row(code: str, pct: float | None) -> dict[str, Any]:
    return {
        "board_code": code,
        "board_name": f"板块{code}",
        "pct_change": pct,
        "amount": 7.0e9,
        "main_net_inflow": 2.0e9,
        "main_net_ratio": 3.0,
        "up_count": 10,
        "flat_count": 1,
        "down_count": 3,
        "lead_stock_name": "龙头",
        "lead_stock_code": "600000",
        "lead_stock_pct": 5.0,
    }


def _patch_em(monkeypatch: pytest.MonkeyPatch, client: Any) -> None:
    monkeypatch.setattr(market_service, "get_eastmoney_client", lambda: client)


def _patch_today(monkeypatch: pytest.MonkeyPatch, day: date = D_TODAY) -> None:
    monkeypatch.setattr(market_service, "_today_sh", lambda: day)


def _patch_snapshot(
    monkeypatch: pytest.MonkeyPatch, found: tuple[date, list[dict[str, Any]]] | None
) -> list[Any]:
    """打桩"读当日快照表" seam；返回记录的 ``(category, expected_day)``。"""
    calls: list[Any] = []

    async def _read(category: str, expected_day: date) -> Any:
        calls.append((category, expected_day))
        return found

    monkeypatch.setattr(market_service, "_hot_boards_snapshot_rows", _read)
    return calls


def _patch_full_capture(monkeypatch: pytest.MonkeyPatch, present: bool) -> list[Any]:
    """打桩"该 (day, dimension) 是否已有 eod_full 行"；记录 ``(day, capture_kind)``。"""
    calls: list[Any] = []

    async def _has(db: Any, day: date, capture_kind: str = "eod_full") -> bool:
        calls.append((day, capture_kind))
        return present

    monkeypatch.setattr(mds.market_data_repo, "sector_moneyflow_day_has_capture", _has)
    return calls


# ---------------------------------------------------------------------------
# 1) 客户端：一次请求、fields 超集、2 列映射；EOD 分页全量
# ---------------------------------------------------------------------------


def _flow_payload() -> dict[str, Any]:
    return {
        "rc": 0,
        "data": {
            "diff": [
                {
                    "f12": "BK1203",
                    "f14": "非银金融",
                    "f3": 0.28,
                    "f6": 7621248698.0,
                    "f62": 2151238400.0,
                    "f66": 1925688320.0,
                    "f72": 225550080.0,
                    "f104": 48,
                    "f105": 26,
                    "f106": 3,
                    "f184": 4.15,
                    "f128": "中信证券",
                    "f136": 5.21,
                    "f140": "600030",
                },
                {  # 上游对无效值给 "-"（板块无成分股/停牌）
                    "f12": "BK9999",
                    "f14": "空板块",
                    "f3": "-",
                    "f6": "-",
                    "f62": "-",
                    "f66": "-",
                    "f72": "-",
                    "f104": "-",
                    "f105": "-",
                    "f106": "-",
                    "f184": "-",
                    "f128": "-",
                    "f136": "-",
                    "f140": "-",
                },
            ]
        },
    }


async def test_fetch_sector_moneyflow_asks_for_amount_and_flat_in_one_request() -> None:
    """``fields`` 必须含 f6/f106（新）+ f66/f72（既有的超大单/大单，丢了会回归资金流卡片）。

    "一次请求拿全"是 ruling #2 的硬约束：为了这 2 列再发一次 ``clist`` 是禁止的 ——
    断言调用次数恰好 1。
    """
    from app.core.providers.eastmoney_client import EastmoneyClient
    from tests.test_eastmoney_client import _FakeEM

    client = EastmoneyClient()
    fake = _FakeEM({"get": _flow_payload()})
    client.__dict__["_get_json"] = fake._get_json

    rows = await client.fetch_sector_moneyflow("industry")

    assert len(fake.calls) == 1, f"必须只有一次 clist 请求，实际 {len(fake.calls)}"
    fields = set(str(fake.calls[0][1]["fields"]).split(","))
    assert {"f6", "f106", "f66", "f72"} <= fields, fields
    # 既有映射一个都不能少（超集断言的反面：丢字段）
    assert set(rows[0]) >= {
        "board_code",
        "board_name",
        "pct_change",
        "amount",
        "main_net_inflow",
        "main_net_ratio",
        "super_large_net",
        "large_net",
        "up_count",
        "flat_count",
        "down_count",
        "lead_stock_name",
        "lead_stock_code",
        "lead_stock_pct",
    }
    assert rows[0]["amount"] == 7621248698.0
    assert rows[0]["flat_count"] == 3
    assert rows[0]["super_large_net"] == 1925688320.0
    assert rows[0]["large_net"] == 225550080.0
    # 上游 "-" → NULL，绝不 0（家数字段统一走 `_count`：原样透传会落进 Integer 列炸掉）
    assert rows[1]["amount"] is None
    assert rows[1]["flat_count"] is None
    assert rows[1]["up_count"] is None and rows[1]["down_count"] is None
    assert rows[1]["pct_change"] is None and rows[1]["main_net_inflow"] is None


async def test_fetch_sector_moneyflow_full_paginates_until_upstream_total() -> None:
    """EOD 全量必须翻页（``pz`` 服务端上限 100）：3 页 250 行 → 250 行 + ``total=250``。

    Ruling 11：15:05 用分页全量（industry ≈5 页 / concept ≈6 页 / region 1 页）。
    这里同时钉住"恰好请求到覆盖 total 为止"（不多不少）。
    """
    from app.core.providers.eastmoney_client import EastmoneyClient

    pages = {
        1: {"data": {"total": 250, "diff": [_raw_flow_row(f"BK{i:04d}") for i in range(100)]}},
        2: {"data": {"total": 250, "diff": [_raw_flow_row(f"BK{i:04d}") for i in range(100, 200)]}},
        3: {"data": {"total": 250, "diff": [_raw_flow_row(f"BK{i:04d}") for i in range(200, 250)]}},
    }
    client = EastmoneyClient()
    fake = _PagedEM(pages)
    client.__dict__["_get_json"] = fake

    rows, total = await client.fetch_sector_moneyflow_full("industry")

    assert (len(rows), total) == (250, 250)
    assert [c["pn"] for c in fake.calls] == [1, 2, 3], "覆盖 total 后不得再多请求一页"
    assert all(c["pz"] == 100 for c in fake.calls)
    fields = set(str(fake.calls[0]["fields"]).split(","))
    assert {"f6", "f106", "f66", "f72"} <= fields, fields


async def test_fetch_sector_moneyflow_full_region_is_a_single_page() -> None:
    """地域 whole-universe 只有 31 个 → 一页即收敛（total=31）。"""
    from app.core.providers.eastmoney_client import EastmoneyClient

    client = EastmoneyClient()
    fake = _PagedEM(
        {1: {"data": {"total": 31, "diff": [_raw_flow_row(f"BK{i:04d}") for i in range(31)]}}}
    )
    client.__dict__["_get_json"] = fake

    rows, total = await client.fetch_sector_moneyflow_full("region")

    assert (len(rows), total, len(fake.calls)) == (31, 31, 1)


async def test_fetch_sector_moneyflow_full_reuses_the_poll_mapper() -> None:
    """全量路径与单次请求路径必须**同一个 mapper**（口径不能分叉）。"""
    from app.core.providers.eastmoney_client import EastmoneyClient

    raw = _raw_flow_row("BK1203", pct=7.86)
    payload = {"data": {"total": 1, "diff": [raw]}}
    poll_client = EastmoneyClient()
    poll_client.__dict__["_get_json"] = _PagedEM({1: payload})
    full_client = EastmoneyClient()
    full_client.__dict__["_get_json"] = _PagedEM({1: payload})

    poll_rows = await poll_client.fetch_sector_moneyflow("industry")
    full_rows, total = await full_client.fetch_sector_moneyflow_full("industry")

    assert full_rows == poll_rows
    assert total == 1
    assert full_rows[0]["amount"] == 1000.0 and full_rows[0]["flat_count"] == 2


async def test_fetch_sector_moneyflow_full_warns_and_reports_short_coverage(caplog) -> None:
    """覆盖不足（第 2 页回空页、total 仍报 496）→ 必须 WARNING + 把 total 交回调用方。

    这是"不许静默截断"的唯一可观测面：短列表看起来完全正常，但当日"全量"其实是子集。
    """
    from app.core.providers.eastmoney_client import EastmoneyClient

    pages = {
        1: {"data": {"total": 496, "diff": [_raw_flow_row(f"BK{i:04d}") for i in range(100)]}},
        2: {"data": {"total": 496, "diff": []}},
    }
    client = EastmoneyClient()
    client.__dict__["_get_json"] = _PagedEM(pages)

    with caplog.at_level(logging.WARNING):
        rows, total = await client.fetch_sector_moneyflow_full("industry")

    assert (len(rows), total) == (100, 496)
    assert "clist paging incomplete" in caplog.text


async def test_fetch_sector_moneyflow_full_rejects_unknown_dimension() -> None:
    from app.core.providers.eastmoney_client import EastmoneyClient

    client = EastmoneyClient()
    client.__dict__["_get_json"] = _PagedEM({})

    with pytest.raises(ValueError, match="industry\\|concept\\|region"):
        await client.fetch_sector_moneyflow_full("sector")


# ---------------------------------------------------------------------------
# 2) repo：2 新列写路径 + 缺失 NULL + 读路径排序
# ---------------------------------------------------------------------------


async def test_upsert_sector_moneyflow_writes_the_two_new_columns() -> None:
    """upsert 的 INSERT 列与 ON CONFLICT 的 SET 都必须带 amount / flat_count。

    e2e 侧另有真库回读；这里是默认门禁的形状守卫（编译 PG 方言 SQL）。
    """
    db = _RecordingDb()

    await market_data_repo.upsert_sector_moneyflow(
        db, D_TODAY, "industry", [dict(_hot_board_row())]
    )

    assert len(db.statements) == 1
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "amount" in sql, sql
    assert "flat_count" in sql, sql
    # SET 子句里也要有（否则盘中重跑不会刷新这 2 列）
    set_clause = sql.split("DO UPDATE SET", 1)[1]
    assert "amount = excluded.amount" in set_clause, set_clause
    assert "flat_count = excluded.flat_count" in set_clause, set_clause


async def test_upsert_sector_moneyflow_keeps_missing_values_as_null() -> None:
    """上游没给 amount/flat_count（本地分组回落等）→ 落 NULL，不落 0。"""
    db = _RecordingDb()
    row = _hot_board_row(amount=None, flat_count=None, lead_stock_name=None, lead_stock_pct=None)

    await market_data_repo.upsert_sector_moneyflow(db, D_TODAY, "industry", [row])

    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    assert [v for k, v in params.items() if k.startswith("amount")] == [None]
    assert [v for k, v in params.items() if k.startswith("flat_count")] == [None]


async def test_upsert_sector_moneyflow_converts_amount_float_to_decimal() -> None:
    """``Numeric(20,4)`` 列必须拿到 ``Decimal``（asyncpg 的 numeric 编码器不收 float）。"""
    db = _RecordingDb()

    await market_data_repo.upsert_sector_moneyflow(
        db, D_TODAY, "industry", [_hot_board_row(amount=7621248698.0)]
    )

    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    amounts = [v for k, v in params.items() if k.startswith("amount")]
    assert amounts == [Decimal("7621248698.0")], amounts


async def test_upsert_sector_moneyflow_records_capture_kind() -> None:
    """``capture_kind`` 必须进 INSERT 列并带上本次调用的值（行级产地：poll/eod_full）。"""
    db = _RecordingDb()

    await market_data_repo.upsert_sector_moneyflow(
        db,
        D_TODAY,
        "industry",
        [dict(_hot_board_row())],
        capture_kind="eod_full",
    )

    stmt = db.statements[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    insert_cols = sql.split(") VALUES", 1)[0]
    assert "capture_kind" in insert_cols, insert_cols
    # INSERT 的绑定值 + SET 里 CASE 的绑定值都必须是本次调用的产地
    bound = [
        v
        for k, v in stmt.compile(dialect=postgresql.dialect()).params.items()
        if k.startswith("capture_kind")
    ]
    assert bound and set(bound) == {"eod_full"}, bound


async def test_upsert_sector_moneyflow_never_downgrades_eod_full() -> None:
    """SET 子句里的 ``capture_kind`` 必须是"已 eod_full 就保持"的 CASE。

    15:05 EOD 之后仍可能撞上一个 15:05 的盘中 tick（或手动轮询）：若它把行改回
    ``'poll'``，T6 的 ``capture_kind='eod_full'`` 过滤当天就会丢行，读表闸门的语义也会漂。
    """
    db = _RecordingDb()

    await market_data_repo.upsert_sector_moneyflow(
        db, D_TODAY, "industry", [dict(_hot_board_row())], capture_kind="poll"
    )

    set_clause = str(db.statements[0].compile(dialect=postgresql.dialect())).split(
        "DO UPDATE SET", 1
    )[1]
    assert "CASE WHEN" in set_clause, set_clause
    assert "excluded.capture_kind" in set_clause, set_clause


async def test_upsert_sector_moneyflow_rejects_unknown_capture_kind() -> None:
    """拼错的 capture_kind 必须当场炸 —— 否则闸门/过滤会静默失效（谁都筛选不到）。"""
    with pytest.raises(ValueError, match="capture_kind"):
        await market_data_repo.upsert_sector_moneyflow(
            _RecordingDb(), D_TODAY, "industry", [dict(_hot_board_row())], capture_kind="nightly"
        )


async def test_list_sector_moneyflow_ranked_is_day_scoped_and_deterministic() -> None:
    """读表分支的行序由 repo SQL 保证：``pct_change DESC NULLS LAST, board_code ASC``。

    同时钉住 ``capture_kind`` 过滤：读表分支只服务"当日全量捕获"那一批行。
    """
    db = _RecordingDb(_EmptyResult())

    await market_data_repo.list_sector_moneyflow_ranked(
        db, D_TODAY, "industry", 100, capture_kind="eod_full"
    )

    assert len(db.statements) == 1
    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "WHERE" in sql
    assert "sector_moneyflow_snapshots.trade_date" in sql
    assert "sector_moneyflow_snapshots.dimension" in sql
    assert "sector_moneyflow_snapshots.capture_kind" in sql
    order = sql.split("ORDER BY", 1)[1]
    assert "sector_moneyflow_snapshots.pct_change DESC NULLS LAST" in order, order
    assert "sector_moneyflow_snapshots.board_code ASC" in order, order
    assert "LIMIT" in sql


async def test_list_sector_moneyflow_range_is_a_plain_window_read() -> None:
    """Task 6 的窗口读：按 ``trade_date, board_code`` 排序，区间闭区间，无 limit。

    不给 ``capture_kind`` = 全量行（含盘中 poll）—— 这里是 Task 6 的"全序列"默认。
    """
    db = _RecordingDb(_EmptyResult())

    await market_data_repo.list_sector_moneyflow_range(db, "industry", D_PREV, D_TODAY)

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    order = sql.split("ORDER BY", 1)[1]
    assert "sector_moneyflow_snapshots.trade_date ASC" in order, order
    assert "sector_moneyflow_snapshots.board_code ASC" in order, order
    assert "LIMIT" not in sql
    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    assert D_PREV in params.values() and D_TODAY in params.values()
    assert ">=" in sql and "<=" in sql
    where = sql.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "capture_kind" not in where, "不给 capture_kind = 不过滤"


async def test_list_sector_moneyflow_range_can_restrict_to_full_capture_days() -> None:
    """``capture_kind='eod_full'`` → 只取收盘全量行（T6 的"完整日"口径）。"""
    db = _RecordingDb(_EmptyResult())

    await market_data_repo.list_sector_moneyflow_range(
        db, "industry", D_PREV, D_TODAY, capture_kind="eod_full"
    )

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "sector_moneyflow_snapshots.capture_kind" in sql
    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    assert "eod_full" in params.values()


async def test_sector_moneyflow_day_has_capture_filters_by_kind() -> None:
    """15:35 补写判据 = "当日有没有 **eod_full** 行"，不是"有没有行"。"""
    db = _RecordingDb(_EmptyResult())

    assert await market_data_repo.sector_moneyflow_day_has_capture(db, D_TODAY, "eod_full") is False

    sql = str(db.statements[0].compile(dialect=postgresql.dialect()))
    assert "sector_moneyflow_snapshots.capture_kind" in sql
    assert "LIMIT" in sql
    params = db.statements[0].compile(dialect=postgresql.dialect()).params
    assert "eod_full" in params.values()


# ---------------------------------------------------------------------------
# 3) service：读表优先的日判据（最近预期交易日）
# ---------------------------------------------------------------------------


async def test_load_hot_board_snapshot_requires_the_expected_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表内该维度最近日 != 预期交易日 → None（调用方回落实时，不服务陈旧行）。"""
    seen: list[Any] = []

    async def _ranked(
        db: Any, day: date, dimension: str, limit: int, *, capture_kind: str
    ) -> list[Any]:
        seen.append((day, dimension, limit, capture_kind))
        return [_snap()]

    monkeypatch.setattr(mds, "_latest_snapshot_day", _async_return(D_PREV))
    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow_ranked", _ranked)
    monkeypatch.setattr(
        mds.market_data_repo, "sector_moneyflow_day_has_capture", _async_return(True)
    )
    db = object()

    assert await mds.load_hot_board_snapshot(db, "industry", D_TODAY) is None
    assert seen == [], "日不匹配时不得再去读明细"

    found = await mds.load_hot_board_snapshot(db, "industry", D_PREV)

    assert found is not None
    day, rows = found
    assert day == D_PREV
    assert seen == [(D_PREV, "industry", mds.HOT_BOARD_SNAPSHOT_LIMIT, "eod_full")]
    assert rows[0]["board_code"] == "BK1211"


async def test_load_hot_board_snapshot_requires_an_eod_full_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**读表闸门**：当日只有盘中轮询行（inflow Top-100 子集）→ None，回落实时。

    Ruling 11：盘中表里只有 f62 Top-100，拿它冒充 f3（涨幅）全量榜是错的，所以只有当日
    存在 ``eod_full``（15:05 分页全量）行时才服务表行。
    """
    detail: list[Any] = []

    async def _ranked(
        db: Any, day: date, dimension: str, limit: int, *, capture_kind: str
    ) -> list[Any]:
        detail.append((day, dimension, capture_kind))
        return [_snap()]

    monkeypatch.setattr(mds, "_latest_snapshot_day", _async_return(D_TODAY))
    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow_ranked", _ranked)
    gate = _patch_full_capture(monkeypatch, False)

    assert await mds.load_hot_board_snapshot(object(), "industry", D_TODAY) is None
    assert gate == [(D_TODAY, "eod_full")], gate
    assert detail == [], "闸门未过时不得再读明细"


async def test_load_hot_board_snapshot_no_rows_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """表内该维度一行都没有 → None（不是空 items：调用方必须回落实时）。"""
    monkeypatch.setattr(mds, "_latest_snapshot_day", _async_return(None))
    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow_ranked", _async_return([]))

    assert await mds.load_hot_board_snapshot(object(), "industry", D_TODAY) is None


async def test_load_hot_board_snapshot_empty_detail_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """日匹配 + 闸门过了但明细为空（竞态/被清）→ 仍 None：绝不把空表当"当日无热点板块"。"""
    monkeypatch.setattr(mds, "_latest_snapshot_day", _async_return(D_TODAY))
    monkeypatch.setattr(mds.market_data_repo, "list_sector_moneyflow_ranked", _async_return([]))
    _patch_full_capture(monkeypatch, True)

    assert await mds.load_hot_board_snapshot(object(), "industry", D_TODAY) is None


async def test_snapshot_row_amount_is_float_not_decimal() -> None:
    """``amount`` 出参必须是 ``float``：payload 进 Redis 时 ``orjson(default=str)`` 会把
    ``Decimal`` 编成**字符串**，缓存命中后前端拿到 ``"123456.789"`` 而不是数字 ——
    实时路径给的是 float，两条路径必须同型。"""
    row = _hot_board_row(amount=Decimal("7621248698.0000"))

    assert row["amount"] == 7621248698.0
    assert isinstance(row["amount"], float)
    assert row["flat_count"] == 11


async def test_snapshot_row_missing_values_stay_none() -> None:
    row = _hot_board_row(amount=None, flat_count=None)

    assert row["amount"] is None
    assert row["flat_count"] is None


async def test_snapshot_row_main_net_inflow_matches_the_sector_moneyflow_mapper() -> None:
    """计划验收项的单元级版本：同一行在两条读路径下的资金口径必须相等。

    ``/market/sector-moneyflow`` 走 :func:`_map_sector_moneyflow_row`，
    ``/market/hot-boards`` 走 :func:`_hot_board_snapshot_row`；两者对同一行必须给出
    相同的 ``main_net_inflow``（真库端到端在 e2e 段的 acceptance 里另测）。
    """
    snap = _snap()
    flow = mds._map_sector_moneyflow_row(snap)
    hot = _hot_board_row()

    assert flow["main_net_inflow"] == hot["main_net_inflow"] == 3.7e9
    assert flow["board_code"] == hot["board_code"] == "BK1211"


async def test_snapshot_row_keys_cover_the_fields_hot_board_items_need() -> None:
    """行 dict 的键集必须覆盖 :func:`_hot_board_item` 读的全部列（缺键 → 静默 None）。"""
    assert set(_hot_board_row()) >= {
        "board_code",
        "board_name",
        "pct_change",
        "up_count",
        "flat_count",
        "down_count",
        "main_net_inflow",
        "main_net_ratio",
        "amount",
        "lead_stock_name",
        "lead_stock_code",
        "lead_stock_pct",
    }


# ---------------------------------------------------------------------------
# 4) get_hot_boards：读表优先 / 回落两分支
# ---------------------------------------------------------------------------


async def test_hot_boards_serves_from_the_snapshot_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """表内有当日行 → 服务表行；东财**一次都不打**。"""
    _patch_today(monkeypatch)
    em = _StaticEM([_live_row("BK9999", 99.0)])
    _patch_em(monkeypatch, em)
    rows = [
        _hot_board_row(board_code="BK0002", board_name="第二", pct_change=2.0),
        _hot_board_row(board_code="BK0001", board_name="第一", pct_change=9.0),
    ]
    calls = _patch_snapshot(monkeypatch, (D_TODAY, rows))
    cache = _RecordingCache()

    payload = await market_service.get_hot_boards("industry", cache)

    assert calls == [("industry", D_TODAY)], "预期交易日 = last_weekday(今天)"
    assert em.calls == [], "读表命中不得再问上游"
    assert payload["source"] == "eastmoney_boards"
    assert payload["degraded_reason"] is None
    assert payload["as_of"] == D_TODAY.isoformat()
    # 实时/收盘快照类 → partial（不是库内判据日的 complete；与实时分支同口径）
    assert payload["as_of_quality"] == "partial"
    assert payload["as_of_reason"] is None
    assert [item["code"] for item in payload["items"]] == ["BK0001", "BK0002"]
    assert payload["items"][0]["mainNetInflow"] == 3.7e9
    assert cache.writes == [
        (
            f"market:hot-boards:em:industry:{D_TODAY.isoformat()}",
            payload,
            market_service._REALTIME_CACHE_TTL,
        )
    ]


async def test_hot_boards_snapshot_items_cover_the_frontend_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读表 items ⊇ 前端 ``HotBoardItem`` / ``HotBoardLeader``（字段名从 TS 真解析）。"""
    _patch_today(monkeypatch)
    _patch_snapshot(monkeypatch, (D_TODAY, [_hot_board_row()]))

    payload = await market_service.get_hot_boards("industry", None)

    item = payload["items"][0]
    assert set(item) >= _ts_interface_fields("HotBoardItem"), set(item)
    assert set(item["leaders"][0]) >= _ts_interface_fields("HotBoardLeader")
    # 2 列新字段是真的有值（不是"键在但恒 None"）
    assert item["amount"] == 123456.789
    assert item["flatCount"] == 11
    assert isinstance(item["amount"], float)


async def test_hot_boards_snapshot_tiebreak_keeps_board_code_ascending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """同 ``pct_change`` 时按 ``board_code`` 升序（SQL 的确定性 tiebreak）。

    行序契约在 repo SQL（``ORDER BY pct_change DESC NULLS LAST, board_code ASC``），
    这里是服务层侧的组合证明：稳定排序（pct desc）+ 已排好的输入 = 确定性输出。
    """
    _patch_today(monkeypatch)
    rows = [
        _hot_board_row(board_code="BK0001", pct_change=5.0),
        _hot_board_row(board_code="BK0002", pct_change=5.0),
        _hot_board_row(board_code="BK0003", pct_change=5.0),
        _hot_board_row(board_code="BK0000", pct_change=9.0),
    ]
    _patch_snapshot(monkeypatch, (D_TODAY, rows))

    payload = await market_service.get_hot_boards("industry", None)

    assert [item["code"] for item in payload["items"]] == ["BK0000", "BK0001", "BK0002", "BK0003"]


async def test_hot_boards_snapshot_day_is_not_relabelled_as_today(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读表分支的 ``as_of`` 恒等于表行日，不被改标成"今天"（ruling #4 诚实性）。"""
    _patch_today(monkeypatch)
    _patch_snapshot(monkeypatch, (D_PREV, [_hot_board_row()]))

    payload = await market_service.get_hot_boards("industry", None)

    assert payload["as_of"] == D_PREV.isoformat()
    assert payload["as_of"] != D_TODAY.isoformat()
    assert payload["as_of_quality"] == "partial"


async def test_hot_boards_falls_back_to_live_when_the_day_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表内没有当日行 → 回落实时抓取（**绝不返回空表**），envelope 仍是实时口径。"""
    _patch_today(monkeypatch)
    calls = _patch_snapshot(monkeypatch, None)
    em = _StaticEM([_live_row("BK0001", 2.5)])
    _patch_em(monkeypatch, em)

    payload = await market_service.get_hot_boards("industry", None)

    assert calls == [("industry", D_TODAY)]
    assert em.calls == ["industry"], "缺行必须回落实时"
    assert payload["items"], "缺行时返回空表 = 把'job 还没跑'谎报成'今天没有热门板块'"
    assert payload["source"] == "eastmoney_boards"
    assert payload["as_of"] == D_TODAY.isoformat()
    assert payload["as_of_quality"] == "partial"


async def test_hot_boards_snapshot_db_error_degrades_to_live_not_500(
    monkeypatch: pytest.MonkeyPatch, caplog: Any
) -> None:
    """读表查询抛 DB 错误（缺 `capture_kind` 列的落后库）→ 视为"今日无快照"、回落实时。

    最终评审 Important 3：旧实现把读表查询放在端点 `try` **之外**，于是一版落后的库上
    （`capture_kind` 由迁移 `a3f8c2d6e9b1` 引入）从"降级"变成 **500**。这条钉住
    `_hot_boards_snapshot_rows` 的宽 except：异常被吞成 `None`，实时路径照常返回。
    这里替换的是更内层的 `load_hot_board_snapshot`（**不**替换
    `_hot_boards_snapshot_rows` 本身），否则不会经过被测的那层 try。
    """
    from sqlalchemy.exc import ProgrammingError

    _patch_today(monkeypatch)
    em = _StaticEM([_live_row("BK0001", 5.0)])
    _patch_em(monkeypatch, em)

    async def _missing_column(db: Any, category: str, expected_day: date) -> Any:
        raise ProgrammingError(
            "SELECT ... capture_kind ...",
            None,
            Exception("UndefinedColumnError: column sector_moneyflow_snapshots.capture_kind"),
        )

    monkeypatch.setattr(mds, "load_hot_board_snapshot", _missing_column)

    with caplog.at_level(logging.WARNING):
        payload = await market_service.get_hot_boards("industry", None)

    assert "snapshot table read failed" in caplog.text
    assert em.calls == ["industry"], "DB 错误必须回落实时抓取"
    assert payload["source"] == "eastmoney_boards"
    assert payload["items"], "不得因读表失败返回空表/5xx"
    assert payload["as_of"] == D_TODAY.isoformat()


async def test_hot_boards_expected_day_is_the_last_weekday(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """周末请求：预期交易日回落到周五（表内有周五行 → 服务周五行，as_of=周五）。"""
    _patch_today(monkeypatch, D_SAT)
    calls = _patch_snapshot(monkeypatch, (D_TODAY, [_hot_board_row()]))

    payload = await market_service.get_hot_boards("industry", None)

    assert calls == [("industry", D_TODAY)], "周六的预期交易日是周五"
    assert payload["as_of"] == D_TODAY.isoformat()


async def test_hot_boards_live_fallback_still_marked_when_eastmoney_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读表缺行 + 东财也炸 → 仍是既有本地分组降级（读表分支不得改变这条语义）。"""
    _patch_today(monkeypatch)
    _patch_snapshot(monkeypatch, None)

    def _boom() -> Any:
        raise RuntimeError("eastmoney unavailable (patched)")

    monkeypatch.setattr(market_service, "get_eastmoney_client", _boom)
    monkeypatch.setattr(
        market_service.market_day_service,
        "resolve_latest_complete_day",
        _async_return(None),
    )
    monkeypatch.setattr(market_service, "load_day_rows", _async_return([]))

    payload = await market_service.get_hot_boards("industry", None)

    assert payload["source"] == "local_grouping"
    assert payload["degraded_reason"] == "eastmoney_unavailable"


async def test_hot_boards_read_from_table_never_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读端点不得写库：读表分支下 upsert 一次都不能被调到（唯一写路径是 job/worker）。"""
    _patch_today(monkeypatch)
    _patch_snapshot(monkeypatch, (D_TODAY, [_hot_board_row()]))

    async def _boom(*_a: Any, **_k: Any) -> int:
        raise AssertionError("读端点不得写快照表")

    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", _boom)

    payload = await market_service.get_hot_boards("industry", None)

    assert payload["items"]


# ---------------------------------------------------------------------------
# 5) 15:05 全量 EOD + 15:35 缺 eod_full 才补写（跨日不承诺）
# ---------------------------------------------------------------------------


async def test_eod_full_ingest_pages_every_dimension_and_marks_eod_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """15:05 全量捕获：三维度各走分页全量，落库 mark ``eod_full``，``fetched``/``total`` 回传。"""
    fetched: list[str] = []

    async def _full(dimension: str) -> tuple[list[dict[str, Any]], int]:
        fetched.append(dimension)
        rows = [_hot_board_row(board_code=f"BK{dimension[:2].upper()}{i:04d}") for i in range(3)]
        return rows, 3

    upserts: list[tuple[date, str, int, str]] = []

    async def _upsert(
        db: Any, trade_date: date, dimension: str, rows: list[Any], *, capture_kind: str = "poll"
    ) -> int:
        upserts.append((trade_date, dimension, len(rows), capture_kind))
        return len(rows)

    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    monkeypatch.setattr(
        mds, "_get_eastmoney", lambda: SimpleNamespace(fetch_sector_moneyflow_full=_full)
    )
    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", _upsert)

    out = await mds.ingest_sector_moneyflow_eod_full(object(), now=D_AFTER_CLOSE)

    assert fetched == ["industry", "concept", "region"]
    assert upserts == [
        (D_TODAY, "industry", 3, "eod_full"),
        (D_TODAY, "concept", 3, "eod_full"),
        (D_TODAY, "region", 3, "eod_full"),
    ]
    assert out["industry"] == {"fetched": 3, "total": 3, "upserted": 3}
    assert mds.eod_shortfalls(out) == []
    assert mds.eod_alert_reasons(out) == []


def test_is_after_close_boundaries_and_timezone_independence() -> None:
    """纯函数（评审 Important #1）：14:59 → False、**15:00 → True**；aware 输入换算到上海。

    naive 视为上海本地（与 ``_today_sh`` 同约定）；``close`` 可注入。
    """
    from datetime import time

    sh = ZoneInfo("Asia/Shanghai")
    assert mds.is_after_close(datetime(2026, 10, 12, 14, 59, tzinfo=sh)) is False
    assert mds.is_after_close(datetime(2026, 10, 12, 15, 0, tzinfo=sh)) is True
    assert mds.is_after_close(datetime(2026, 10, 12, 15, 0, 1, tzinfo=sh)) is True
    # aware 输入不要求是上海时区：UTC 06:59 == 上海 14:59；UTC 07:00 == 上海 15:00
    assert mds.is_after_close(datetime(2026, 10, 12, 6, 59, tzinfo=UTC)) is False
    assert mds.is_after_close(datetime(2026, 10, 12, 7, 0, tzinfo=UTC)) is True
    # naive 视作上海本地时间
    assert mds.is_after_close(datetime(2026, 10, 12, 14, 59)) is False
    assert mds.is_after_close(datetime(2026, 10, 12, 15, 0)) is True
    # close 可注入（将来收盘时间改了不用动调用点）
    assert mds.is_after_close(datetime(2026, 10, 12, 14, 30, tzinfo=sh), close=time(14, 0)) is True


async def test_eod_full_ingest_skips_before_close_without_any_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**盘中（<15:00）调用 → 零写入 + 零外呼** + reason ``before_close``（且告警可见）。

    评审 Important #1 的两条可达路径之一（worker 手动入口盘中触发）：若允许盘中打
    ``eod_full``，它单向不可降级 → 读表闸门从那一刻起整天打开，而表里只有 grab 时刻的
    子集 → 正是 Ruling 11 要消除的缺陷。
    """
    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    monkeypatch.setattr(mds, "_get_eastmoney", _boom_factory("收盘前不得外呼东财"))
    monkeypatch.setattr(
        mds.market_data_repo,
        "upsert_sector_moneyflow",
        _raise_factory("收盘前不得写任何行"),
    )

    out = await mds.ingest_sector_moneyflow_eod_full(object(), now=D_BEFORE_CLOSE)

    assert out["skipped"] is True
    assert out["reason"] == "before_close"
    assert out["trade_date"] == D_TODAY.isoformat()
    assert mds.eod_shortfalls(out) == []
    reasons = mds.eod_alert_reasons(out)
    assert reasons and "before_close" in reasons[0]


async def test_eod_full_ingest_writes_at_the_close_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """15:00 整（含）之后照常写入 —— 守卫不得误伤边界与收盘后的补跑。"""
    upserts: list[str] = []

    async def _full(dimension: str) -> tuple[list[dict[str, Any]], int]:
        return [_hot_board_row(board_code=f"BK{dimension[:2].upper()}")], 1

    async def _upsert(
        db: Any, trade_date: date, dimension: str, rows: list[Any], *, capture_kind: str = "poll"
    ) -> int:
        upserts.append(capture_kind)
        return len(rows)

    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    monkeypatch.setattr(
        mds, "_get_eastmoney", lambda: SimpleNamespace(fetch_sector_moneyflow_full=_full)
    )
    monkeypatch.setattr(mds.market_data_repo, "upsert_sector_moneyflow", _upsert)

    out = await mds.ingest_sector_moneyflow_eod_full(object(), now=D_AT_CLOSE)

    assert upserts == ["eod_full", "eod_full", "eod_full"]
    assert not out.get("skipped")
    assert mds.eod_alert_reasons(out) == []


async def test_eod_shortfalls_flags_incomplete_and_unknown_coverage() -> None:
    """纯函数判据（job 与 worker 共用）：覆盖不足 / 上游没报 total 都要可见。"""
    assert mds.eod_shortfalls({"industry": {"fetched": 496, "total": 496}}) == []
    assert mds.eod_shortfalls({"industry": {"fetched": 95, "total": 496}}) == [
        "industry: fetched=95 < total=496"
    ]
    assert mds.eod_shortfalls({"concept": {"fetched": 100, "total": None}}) == [
        "concept: upstream total unknown (fetched=100)"
    ]
    # 15:35 的 skipped 形态没有维度统计 → 不算短缺
    assert mds.eod_shortfalls({"skipped": True, "trade_date": "2026-09-18"}) == []


def test_eod_alert_reasons_includes_before_close_skip() -> None:
    """告警通道的单一判据 = 覆盖不足 **+** before_close 跳过（两条轨道共用）。"""
    assert mds.eod_alert_reasons({"skipped": True, "trade_date": "2026-09-18"}) == []
    assert mds.eod_alert_reasons(
        {"industry": {"fetched": 496, "total": 496}, "concept": {"fetched": 1, "total": 506}}
    ) == ["concept: fetched=1 < total=506"]
    before = {"skipped": True, "reason": "before_close", "now": "2026-09-18T14:30:00+08:00"}
    assert mds.eod_alert_reasons(before) == [
        "skipped: before_close (now=2026-09-18T14:30:00+08:00, close=15:00 SH)"
    ]


async def test_rewrite_ingest_skips_when_today_is_already_fully_captured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """当日已有 ``eod_full`` 行 → no-op（不许覆盖已落库的收盘全量）。"""
    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    _patch_full_capture(monkeypatch, True)

    async def _boom(_db: Any) -> dict[str, Any]:
        raise AssertionError("当日已全量捕获 → 补写 job 必须 no-op")

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_eod_full", _boom)

    out = await mds.ingest_sector_moneyflow_if_missing(object())

    assert out["skipped"] is True
    assert out["trade_date"] == D_TODAY.isoformat()
    assert "status" not in out, "worker 结果里 status 由 BaseWorker 拥有（不得被内层覆盖）"
    assert mds.eod_shortfalls(out) == []


async def test_rewrite_ingest_runs_the_full_capture_when_eod_full_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只落了盘中轮询、错过 15:05 → 15:35 **仍要**补做收盘全量（Ruling 11 关闭的缺口）。"""
    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    gate = _patch_full_capture(monkeypatch, False)
    seen: list[Any] = []

    async def _full(db: Any, *, now: Any = None) -> dict[str, Any]:
        seen.append((db, now))
        return {"industry": {"fetched": 496, "total": 496, "upserted": 496}}

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_eod_full", _full)
    db = object()

    out = await mds.ingest_sector_moneyflow_if_missing(db)

    assert gate == [(D_TODAY, "eod_full")], "守卫看的是 eod_full，不是'有没有行'"
    assert [s[0] for s in seen] == [db], "补写必须复用同一个全量捕获方法（双轨制）"
    assert out["skipped"] is False
    assert out["industry"]["fetched"] == 496 and out["trade_date"] == D_TODAY.isoformat()


# ---------------------------------------------------------------------------
# 6) 双轨制接线：schema / worker / scheduler
# ---------------------------------------------------------------------------


def test_job_types_registered() -> None:
    members = set(get_args(MarketDataJobType))
    assert "board_snapshot_eod" in members
    assert "board_snapshot_rewrite" in members


async def test_worker_dispatches_board_snapshot_eod(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def _full(db: Any) -> dict[str, Any]:
        called.append(db)
        return {"industry": {"fetched": 496, "total": 496, "upserted": 496}}

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_eod_full", _full)
    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    from app.workers.market_data_worker import MarketDataWorker

    result = await MarketDataWorker().process(task_id=None, payload={"type": "board_snapshot_eod"})

    assert result["status"] == "completed" and result["type"] == "board_snapshot_eod"
    assert result["industry"]["fetched"] == 496 and called


async def test_worker_board_snapshot_alerts_on_short_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 轨道与定时轨道同款可见性：全量覆盖不足必须进 `job:failures`。"""
    recorded: list[Any] = []

    async def _full(db: Any) -> dict[str, Any]:
        return {"industry": {"fetched": 95, "total": 496, "upserted": 95}}

    async def _record(job_id: str, error: str, **kwargs: Any) -> None:
        recorded.append((job_id, error))

    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_eod_full", _full)
    from app.services import job_alert_service as jas
    from app.workers.market_data_worker import MarketDataWorker

    monkeypatch.setattr(jas, "record_job_failure", _record)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )

    result = await MarketDataWorker().process(task_id=None, payload={"type": "board_snapshot_eod"})

    assert result["status"] == "completed"
    assert recorded and recorded[0][0] == "board_snapshot_eod"
    assert "fetched=95 < total=496" in recorded[0][1]


async def test_worker_dispatches_board_snapshot_rewrite(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[Any] = []

    async def _rewrite(db: Any) -> dict[str, Any]:
        called.append(db)
        return {"skipped": True, "trade_date": D_TODAY.isoformat()}

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_if_missing", _rewrite)
    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    from app.workers.market_data_worker import MarketDataWorker

    result = await MarketDataWorker().process(
        task_id=None, payload={"type": "board_snapshot_rewrite"}
    )

    assert result["status"] == "completed" and result["type"] == "board_snapshot_rewrite"
    assert result["skipped"] is True and called


class _Session:
    async def commit(self) -> None:
        return None


@asynccontextmanager
async def _session_factory() -> AsyncGenerator[_Session, None]:
    yield _Session()


@pytest.mark.parametrize(
    ("job_func", "service_name", "job_id"),
    [
        ("board_snapshot_eod_job", "ingest_sector_moneyflow_eod_full", "board_snapshot_eod"),
        (
            "board_snapshot_rewrite_job",
            "ingest_sector_moneyflow_if_missing",
            "board_snapshot_rewrite",
        ),
    ],
)
async def test_board_snapshot_jobs_call_the_worker_service(
    monkeypatch: pytest.MonkeyPatch, job_func: str, service_name: str, job_id: str
) -> None:
    """双轨制：两个 job 各调 worker 分支**同一个** service 方法，并 commit。"""
    from app.scheduler import jobs as scheduler_jobs

    events: list[str] = []

    async def _service(db: Any) -> dict[str, Any]:
        events.append("service")
        return {"industry": {"fetched": 496, "total": 496, "upserted": 496}}

    monkeypatch.setattr(mds, service_name, _service)
    monkeypatch.setattr("app.core.database.async_session_factory", _session_factory)
    # `_is_workday` 是纯 weekday 判断：不打桩则周末早返回、断言假红
    monkeypatch.setattr(scheduler_jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(scheduler_jobs, "_alert_job_failure", _async_return(None))

    await getattr(scheduler_jobs, job_func)()

    assert events == ["service"], f"{job_id} 必须调 {service_name}"


@pytest.mark.parametrize(
    ("job_func", "job_id"),
    [
        ("board_snapshot_eod_job", "board_snapshot_eod"),
        ("board_snapshot_rewrite_job", "board_snapshot_rewrite"),
    ],
)
async def test_board_snapshot_jobs_alert_on_short_coverage(
    monkeypatch: pytest.MonkeyPatch, job_func: str, job_id: str
) -> None:
    """ "当日全量"其实是子集时必须告警（且 job 不整体失败）—— A 类静默丢失的唯一可观测面。"""
    from app.scheduler import jobs as scheduler_jobs

    async def _service(db: Any) -> dict[str, Any]:
        return {"industry": {"fetched": 12, "total": 496, "upserted": 12}}

    alerts: list[Any] = []

    async def _alert(ids: Any, exc: BaseException) -> None:
        alerts.append((ids, exc))

    monkeypatch.setattr(mds, "ingest_sector_moneyflow_eod_full", _service)
    monkeypatch.setattr(mds, "ingest_sector_moneyflow_if_missing", _service)
    monkeypatch.setattr("app.core.database.async_session_factory", _session_factory)
    monkeypatch.setattr(scheduler_jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(scheduler_jobs, "_alert_job_failure", _alert)

    await getattr(scheduler_jobs, job_func)()

    assert alerts and alerts[0][0] == job_id
    assert "fetched=12 < total=496" in str(alerts[0][1])


@pytest.mark.parametrize(
    ("job_func", "job_id"),
    [
        ("board_snapshot_eod_job", "board_snapshot_eod"),
        ("board_snapshot_rewrite_job", "board_snapshot_rewrite"),
    ],
)
async def test_board_snapshot_jobs_skip_and_alert_before_close(
    monkeypatch: pytest.MonkeyPatch, job_func: str, job_id: str
) -> None:
    """定时轨道也过收盘守卫（评审 Important #1）：钉在 14:30 → 零写入 + ``job:failures``。

    这是"跨日补跑"路径（``misfire_grace_time=None``，宿主睡醒后补跑）：早上触发时若绕过
    守卫，就会把**新的一天**提前写成 ``eod_full``，闸门整天打开。
    """
    from app.scheduler import jobs as scheduler_jobs

    alerts: list[Any] = []

    async def _alert(ids: Any, exc: BaseException) -> None:
        alerts.append((ids, exc))

    monkeypatch.setattr(mds, "_now_sh", lambda: D_BEFORE_CLOSE)
    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    monkeypatch.setattr(mds, "_get_eastmoney", _boom_factory("收盘前不得外呼东财"))
    monkeypatch.setattr(
        mds.market_data_repo, "upsert_sector_moneyflow", _raise_factory("收盘前不得写任何行")
    )
    monkeypatch.setattr(
        mds.market_data_repo, "sector_moneyflow_day_has_capture", _async_return(False)
    )
    monkeypatch.setattr("app.core.database.async_session_factory", _session_factory)
    monkeypatch.setattr(scheduler_jobs, "_is_workday", lambda: True)
    monkeypatch.setattr(scheduler_jobs, "_alert_job_failure", _alert)

    await getattr(scheduler_jobs, job_func)()

    assert alerts, "收盘前跳过必须登记 job:failures（两条轨道同款可见性）"
    assert alerts[0][0] == job_id
    assert "before_close" in str(alerts[0][1])


async def test_worker_board_snapshot_skips_before_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 手动轨道同样过守卫：盘中触发 ``board_snapshot_eod`` → 零写入 + 告警。"""
    recorded: list[Any] = []

    async def _record(job_id: str, error: str, **kwargs: Any) -> None:
        recorded.append((job_id, error))

    from tests.test_market_data_worker import _fake_session_factory

    monkeypatch.setattr(mds, "_now_sh", lambda: D_BEFORE_CLOSE)
    monkeypatch.setattr(mds, "_today_sh", lambda: D_TODAY)
    monkeypatch.setattr(mds, "_get_eastmoney", _boom_factory("收盘前不得外呼东财"))
    monkeypatch.setattr(
        mds.market_data_repo, "upsert_sector_moneyflow", _raise_factory("收盘前不得写任何行")
    )
    monkeypatch.setattr(
        mds.market_data_repo, "sector_moneyflow_day_has_capture", _async_return(False)
    )
    from app.services import job_alert_service as jas
    from app.workers.market_data_worker import MarketDataWorker

    monkeypatch.setattr(jas, "record_job_failure", _record)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )

    eod = await MarketDataWorker().process(task_id=None, payload={"type": "board_snapshot_eod"})
    rewrite = await MarketDataWorker().process(
        task_id=None, payload={"type": "board_snapshot_rewrite"}
    )

    for result, job_id in ((eod, "board_snapshot_eod"), (rewrite, "board_snapshot_rewrite")):
        assert result["status"] == "completed"
        assert result["skipped"] is True and result["reason"] == "before_close"
    assert [r[0] for r in recorded] == ["board_snapshot_eod", "board_snapshot_rewrite"]
    assert all("before_close" in r[1] for r in recorded)


@pytest.mark.parametrize("job_func", ["board_snapshot_eod_job", "board_snapshot_rewrite_job"])
async def test_board_snapshot_jobs_noop_on_non_workday(
    monkeypatch: pytest.MonkeyPatch, job_func: str
) -> None:
    from app.scheduler import jobs as scheduler_jobs

    events: list[str] = []

    async def _service(db: Any) -> dict[str, Any]:
        events.append("service")
        return {}

    for name in (
        "ingest_sector_moneyflow",
        "ingest_sector_moneyflow_eod_full",
        "ingest_sector_moneyflow_if_missing",
    ):
        monkeypatch.setattr(mds, name, _service)
    monkeypatch.setattr("app.core.database.async_session_factory", _session_factory)
    monkeypatch.setattr(scheduler_jobs, "_is_workday", lambda: False)

    await getattr(scheduler_jobs, job_func)()

    assert events == [], "非交易日（周末）不得写快照"


async def test_scheduler_registers_board_snapshot_jobs_with_shanghai_cron() -> None:
    """15:05 收盘**全量** + 15:35 缺 ``eod_full`` 才补写；都 mon-fri / Asia/Shanghai。"""
    from apscheduler.triggers.cron import CronTrigger

    from app.scheduler.runner import create_scheduler

    scheduler = create_scheduler()
    scheduler.start()
    try:
        eod = scheduler.get_job("board_snapshot_eod")
        rewrite = scheduler.get_job("board_snapshot_rewrite")
        assert eod is not None, "board_snapshot_eod job not registered"
        assert rewrite is not None, "board_snapshot_rewrite job not registered"
        for job, hour, minute in ((eod, "15", "5"), (rewrite, "15", "35")):
            assert isinstance(job.trigger, CronTrigger)
            assert str(job.trigger.timezone) == "Asia/Shanghai"
            fields = {f.name: str(f) for f in job.trigger.fields}
            assert fields["day_of_week"] == "mon-fri", fields
            assert fields["hour"] == hour, fields
            assert fields["minute"] == minute, fields
            # 盘后日频任务：继承 job_defaults(None) → 停摆后迟到也补跑（容器重启兜底）
            assert job.misfire_grace_time is None
    finally:
        scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# 7) 真库（`-m e2e`）：2 新列往返 + 读表排序
# ---------------------------------------------------------------------------


@pytest.fixture
async def _clean_scratch_day() -> AsyncGenerator[None, None]:
    """只清理 1999-01-04 这一天的 scratch 行（早于任何真实快照，且不动 max(trade_date)）。"""
    from sqlalchemy import text

    from app.core.database import async_session_factory, engine

    async def _delete() -> None:
        async with async_session_factory() as db:
            await db.execute(
                text("DELETE FROM sector_moneyflow_snapshots WHERE trade_date = :d"),
                {"d": D_ANCIENT},
            )
            await db.commit()

    await _delete()
    try:
        yield
    finally:
        await _delete()
        await engine.dispose()


@pytest.mark.e2e
async def test_snapshot_columns_round_trip_in_pg(_clean_scratch_day: None) -> None:
    """真库：``amount``/``flat_count``/``capture_kind`` 往返；缺值仍 NULL；重跑收敛。

    并证明 **eod_full 不被降级**：全量写入后再来一次盘中 poll upsert（capture_kind='poll'），
    行仍是 ``eod_full``（SET 子句的 CASE 在真 PG 上的行为）。
    """
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.market_data import SectorMoneyflowSnapshot

    rows = [
        {**_hot_board_row(board_code="BK9001", pct_change=1.0), "amount": 1234.5678},
        _hot_board_row(board_code="BK9002", pct_change=1.0, amount=None, flat_count=None),
    ]

    async def _read() -> list[SectorMoneyflowSnapshot]:
        return list(
            (
                await db.execute(
                    select(SectorMoneyflowSnapshot)
                    .where(SectorMoneyflowSnapshot.trade_date == D_ANCIENT)
                    .order_by(SectorMoneyflowSnapshot.board_code)
                )
            )
            .scalars()
            .all()
        )

    async with async_session_factory() as db:
        n = await market_data_repo.upsert_sector_moneyflow(
            db, D_ANCIENT, "industry", rows, capture_kind="eod_full"
        )
        await db.commit()
        assert n == 2
        got = await _read()
        assert [float(r.amount) for r in got[:1]] == [1234.5678]
        assert got[0].flat_count == 11
        assert got[1].amount is None and got[1].flat_count is None, "缺失必须 NULL，不是 0"
        assert {r.capture_kind for r in got} == {"eod_full"}

        # 15:05 之后撞上来的盘中 tick：值可以刷新，capture_kind 不得降级回 poll
        await market_data_repo.upsert_sector_moneyflow(
            db, D_ANCIENT, "industry", rows, capture_kind="poll"
        )
        await db.commit()
        assert {r.capture_kind for r in await _read()} == {"eod_full"}, "eod_full 被降级了"

        # 重跑收敛：同键同值，不产生第 3 行
        await market_data_repo.upsert_sector_moneyflow(db, D_ANCIENT, "industry", rows)
        await db.commit()
        total = (
            await db.execute(
                select(func.count())
                .select_from(SectorMoneyflowSnapshot)
                .where(SectorMoneyflowSnapshot.trade_date == D_ANCIENT)
            )
        ).scalar()
        assert total == 2
        assert await market_data_repo.sector_moneyflow_day_has_capture(db, D_ANCIENT, "eod_full")
        # 粘性：那次 poll upsert 没有把这天变成"有 poll 行"（行级产地保持 eod_full）
        assert not await market_data_repo.sector_moneyflow_day_has_capture(db, D_ANCIENT, "poll")


@pytest.mark.e2e
async def test_list_sector_moneyflow_ranked_orders_in_pg(_clean_scratch_day: None) -> None:
    """真库：行序 = ``pct_change DESC NULLS LAST, board_code ASC``，且**只取 eod_full 行**。"""
    from app.core.database import async_session_factory

    full_rows = [
        _hot_board_row(board_code="BK9003", pct_change=None),
        _hot_board_row(board_code="BK9002", pct_change=5.0),
        _hot_board_row(board_code="BK9001", pct_change=5.0),
        _hot_board_row(board_code="BK9004", pct_change=9.0),
    ]
    poll_row = _hot_board_row(board_code="BK9009", pct_change=99.0)  # 盘中残留：必须被排除

    async with async_session_factory() as db:
        await market_data_repo.upsert_sector_moneyflow(
            db, D_ANCIENT, "industry", full_rows, capture_kind="eod_full"
        )
        await market_data_repo.upsert_sector_moneyflow(
            db, D_ANCIENT, "industry", [poll_row], capture_kind="poll"
        )
        await db.commit()
        got = await market_data_repo.list_sector_moneyflow_ranked(
            db, D_ANCIENT, "industry", 100, capture_kind="eod_full"
        )

    assert [r.board_code for r in got] == ["BK9004", "BK9001", "BK9002", "BK9003"]
    # 区间读（Task 6 seam）默认给全序列；指定 eod_full 则排除盘中残留
    async with async_session_factory() as db:
        window = await market_data_repo.list_sector_moneyflow_range(
            db, "industry", D_ANCIENT, D_ANCIENT
        )
        full_only = await market_data_repo.list_sector_moneyflow_range(
            db, "industry", D_ANCIENT, D_ANCIENT, capture_kind="eod_full"
        )
    assert [r.board_code for r in window] == [
        "BK9001",
        "BK9002",
        "BK9003",
        "BK9004",
        "BK9009",
    ]
    assert [r.board_code for r in full_only] == ["BK9001", "BK9002", "BK9003", "BK9004"]


@pytest.mark.e2e
async def test_eod_full_row_counts_match_upstream_total_in_pg(
    _clean_scratch_day: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """真库：**收盘后**全量抓取的落库行数 == 上游 ``total``（三维度分别核对），闸门随之打开。

    注入 ``now``（scratch 日 15:05 SH）而不是让它取真实当下：收盘守卫会挡住盘中调用
    （见 ``test_eod_full_ingest_skips_before_close_without_any_write``）。
    """
    from sqlalchemy import func, select

    from app.core.database import async_session_factory
    from app.models.market_data import SectorMoneyflowSnapshot

    totals = {"industry": 496, "concept": 506, "region": 31}

    async def _full(dimension: str) -> tuple[list[dict[str, Any]], int]:
        total = totals[dimension]
        rows = [
            _hot_board_row(board_code=f"BK9{i % 9000:04d}", pct_change=float(i % 7))
            for i in range(total)
        ]
        return rows, total

    monkeypatch.setattr(mds, "_today_sh", lambda: D_ANCIENT)
    monkeypatch.setattr(
        mds, "_get_eastmoney", lambda: SimpleNamespace(fetch_sector_moneyflow_full=_full)
    )

    async with async_session_factory() as db:
        out = await mds.ingest_sector_moneyflow_eod_full(
            db, now=datetime(1999, 1, 4, 15, 5, tzinfo=_SH_TZ)
        )
        await db.commit()
        for dimension, total in totals.items():
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(SectorMoneyflowSnapshot)
                    .where(
                        SectorMoneyflowSnapshot.trade_date == D_ANCIENT,
                        SectorMoneyflowSnapshot.dimension == dimension,
                        SectorMoneyflowSnapshot.capture_kind == "eod_full",
                    )
                )
            ).scalar()
            assert count == total, f"{dimension}: rows={count} != upstream total={total}"
        assert await market_data_repo.sector_moneyflow_day_has_capture(db, D_ANCIENT, "eod_full")
        # 闸门打开后：读表分支真的能服务这一天（eod_full 明细非空）
        # `_latest_snapshot_day` 打桩成 scratch 日：共享 dev 库里还有真实交易日（今天）的行，
        # 不打桩会解析到今天而不是 scratch 日（这里要测的是"闸门 + eod_full 过滤 + SQL"）。
        monkeypatch.setattr(mds, "_latest_snapshot_day", _async_return(D_ANCIENT))
        found = await mds.load_hot_board_snapshot(db, "region", D_ANCIENT)
    assert found is not None and len(found[1]) == totals["region"]
    assert out["industry"] == {"fetched": 496, "total": 496, "upserted": 496}
    assert mds.eod_shortfalls(out) == []


class _RecordingCache:
    """读写都记录、`set` 可被后续 `get` 命中的内存缓存替身。"""

    def __init__(self) -> None:
        self.seeds: dict[str, Any] = {}
        self.reads: list[str] = []
        self.writes: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.reads.append(key)
        return self.seeds.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.writes.append((key, value, ttl))
        self.seeds[key] = value
