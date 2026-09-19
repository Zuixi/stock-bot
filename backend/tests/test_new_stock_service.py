"""`GET /api/v1/new-stocks` 契约（T10）：默认门禁钉 KPI/缺失语义与梯队派生。

`-m e2e` 直连真库钉口径。

分两层（与 `test_concept_api.py` 同构）：

- **默认门禁（不连库）**：`build_kpis` 的家数裁决（`never_broken=None` 不可判 ≠ 已开板）、
  梯队 → (`streak`, `is_lu`) 的派生、缺失值不 0 填充、降级词表与"仅非空才缓存"。全部
  monkeypatch 数据源，故 `uv run pytest` 在 dev DB 未启动时也全绿。
- **`-m e2e`（真库 + in-process ASGI app）**：契约字段 + 与 `concept_members` 对拍 +
  `up+flat+down+unpriced == len(items)` 不变量 + `never_broken` 三态类型（绝不为 0/1 整数）。
  期望值全部现查库（BK0501 成分数会随板块刷新变化），不写死 162/157 这类当日数字。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import text

from app.core.database import async_session_factory, engine
from app.core.redis import CacheClient, close_redis_pool, get_redis_pool
from app.main import app
from app.repositories import concept_repo
from app.schemas.concept import NewStockItemOut
from app.schemas.stock import StockEnrichedOut
from app.services import limit_up_service, market_service, new_stock_service

# §2.2 响应键集（前端 T15 `NewStocksResponse`）
NEW_STOCKS_KEYS = {
    "as_of",
    "membership_as_of",
    "board_code",
    "board_name",
    "source",
    "degraded_reason",
    "kpis",
    "items",
}
KPI_KEYS = {
    "up_count",
    "flat_count",
    "down_count",
    "unpriced_count",
    "limit_up_count",
    "unbroken_count",
    "above_first_open_count",
    "avg_pct",
}
ITEM_KEYS = set(NewStockItemOut.model_fields)


def test_kpis_treat_missing_limits_as_unknown_not_broken() -> None:
    """限价缺失时 never_broken 必须是 None（不可判），绝不能是 False。"""
    stats = {"600000": {"listed_trade_days": 5, "never_broken": None, "first_open": 10.0}}
    rows = [{"symbol": "600000", "pct_chg": 3.0, "close": 11.0}]
    kpis = new_stock_service.build_kpis(rows, stats)
    assert kpis["unbroken_count"] == 0
    assert kpis["above_first_open_count"] == 1
    assert rows[0]["never_broken"] is None  # 透传给 UI 渲染 `--`
    assert rows[0]["listed_trade_days"] == 5
    assert rows[0]["first_open"] == 10.0


def test_board_code_constant_is_single_source() -> None:
    """次新股口径 = 东财 BK0501（实测 162 只全为上市 ≤1 年），不得另立时间窗规则。"""
    assert new_stock_service.NEW_STOCK_BOARD_CODE == "BK0501"


def test_kpis_pct_buckets_are_mutually_exclusive_and_sum_to_item_count() -> None:
    """四档家数互斥且覆盖全部 items；`avg_pct` 分母是**非空** pct_chg 家数，不是 len(items)。"""
    items = [
        {"symbol": "000001", "pct_chg": 3.0, "close": 1.0},
        {"symbol": "000002", "pct_chg": -2.0, "close": 1.0},
        {"symbol": "000003", "pct_chg": 0.0, "close": 1.0},
        {"symbol": "000004", "pct_chg": None, "close": None},
    ]
    kpis = new_stock_service.build_kpis(items, {})

    assert (kpis["up_count"], kpis["flat_count"], kpis["down_count"]) == (1, 1, 1)
    assert kpis["unpriced_count"] == 1
    assert kpis["up_count"] + kpis["flat_count"] + kpis["down_count"] + kpis[
        "unpriced_count"
    ] == len(items)
    assert kpis["avg_pct"] == pytest.approx((3.0 - 2.0 + 0.0) / 3)
    assert items[3]["close"] is None, "无行情不得 0 填充"


def test_kpis_avg_pct_none_when_every_pct_missing() -> None:
    """全无行情 → `avg_pct=None`（不是 0），且 `unpriced_count == len(items)`。"""
    items = [{"symbol": "000005", "pct_chg": None, "close": None}]
    kpis = new_stock_service.build_kpis(items, {"000005": {"listed_trade_days": 0}})

    assert kpis["avg_pct"] is None
    assert kpis["unpriced_count"] == 1
    assert kpis["unbroken_count"] == 0 and kpis["above_first_open_count"] == 0
    assert items[0]["never_broken"] is None and items[0]["first_open"] is None
    assert items[0]["above_first_open"] is None


def test_kpis_above_first_open_null_when_either_side_missing() -> None:
    """替代破发口径：`close` 或 `first_open` 任一为 None → `above_first_open=None`（不猜）。"""
    stats = {
        "000006": {"listed_trade_days": 3, "never_broken": True, "first_open": 10.0},
        "000007": {"listed_trade_days": 3, "never_broken": True, "first_open": None},
        "000008": {"listed_trade_days": 3, "never_broken": True, "first_open": 10.0},
    }
    items = [
        {"symbol": "000006", "pct_chg": 1.0, "close": None},  # 无行情
        {"symbol": "000007", "pct_chg": 1.0, "close": 12.0},  # 无首日开盘
        {"symbol": "000008", "pct_chg": 1.0, "close": 9.0},  # 低于首日开盘
    ]
    kpis = new_stock_service.build_kpis(items, stats)

    assert [i["above_first_open"] for i in items] == [None, None, False]
    assert kpis["above_first_open_count"] == 0
    assert kpis["unbroken_count"] == 3, "三条 never_broken=True 都计入（与 above_first_open 无关）"


def test_kpis_only_true_counts_as_unbroken() -> None:
    """`never_broken` 只在 is True 时计入：False（已开板）与 None（不可判）都不算。"""
    items = [{"symbol": "1", "pct_chg": 1.0, "close": 1.0} for _ in range(3)]
    for item, symbol in zip(items, ["1", "2", "3"], strict=True):
        item["symbol"] = symbol
    stats = {
        "1": {"never_broken": True, "first_open": 1.0, "listed_trade_days": 1},
        "2": {"never_broken": False, "first_open": 1.0, "listed_trade_days": 1},
        "3": {"never_broken": None, "first_open": 1.0, "listed_trade_days": 1},
    }
    assert new_stock_service.build_kpis(items, stats)["unbroken_count"] == 1


def test_streak_map_flattens_echelons_and_skips_missing_streak() -> None:
    """梯队拍平：跨档收集 `{symbol: streak}`；`streak` 缺失的行不进表（后续判为缺失）。"""
    echelons = [
        {"streak": 3, "label": "3连板", "stocks": [{"symbol": "601091", "streak": 3}]},
        {
            "streak": 1,
            "label": "首板",
            "stocks": [
                {"symbol": "688837", "streak": 1},
                {"symbol": "301234"},  # 无 streak 字段 → 不猜 0
            ],
        },
    ]
    assert new_stock_service._streak_map(echelons) == {"601091": 3, "688837": 1}
    assert new_stock_service._streak_map([]) == {}


def test_build_items_ladder_absent_streak_is_none_not_zero() -> None:
    """不在梯队里 → `streak=None`、`is_lu=False`（缺失 ≠ 0：0 会被读成"今天连板数为 0"）。"""
    items = new_stock_service._build_items(
        [{"symbol": "601091", "name": "C沈鼓", "exchange": "Shanghai_Stocks"}], {}, {}
    )
    assert items[0]["streak"] is None
    assert items[0]["is_lu"] is False

    present = new_stock_service._build_items(
        [{"symbol": "601091", "name": "C沈鼓", "exchange": "Shanghai_Stocks"}], {"601091": 2}, {}
    )
    assert present[0]["streak"] == 2 and present[0]["is_lu"] is True


def test_build_items_keeps_null_pct_chg_and_price_as_null() -> None:
    """停牌/无行情：`pct_chg`/`close` 原样 null，绝不 0 填充（前端渲染 `--`）。"""
    items = new_stock_service._build_items(
        [
            {
                "symbol": "688837",
                "name": "C信诺维",
                "exchange": "Shanghai_Stocks",
                "list_date": None,
                "latest_price": None,
                "change_percent": None,
                "turnover_rate": None,
                "circ_mv": None,
                "amount": None,
            }
        ],
        {},
        {},
    )
    item = items[0]
    assert item["pct_chg"] is None and item["close"] is None
    assert item["pct_chg"] != 0 and item["close"] != 0
    assert item["list_date"] is None and item["turnover_rate"] is None


def test_degraded_reason_no_members_precedence_and_passthrough() -> None:
    """板内无成分 → `no_members`（优先）；否则透传快照词，`no_limit_up_rows` 例外丢弃。"""
    reason = new_stock_service._degraded_reason
    assert reason(False, "no_quotes") == "no_members", "一级空态优先于快照退化"
    assert reason(False, None) == "no_members"
    assert reason(True, "no_limit_up_rows") is None, "市场级无涨停是合法空态，不透传"
    assert reason(True, "price_limits_missing") == "price_limits_missing"
    assert reason(True, "no_quotes") == "no_quotes"
    assert reason(True, None) is None


# ---------------------------------------------------------------------------
# 默认门禁：service 装配（monkeypatch 全部数据源，不触 DB）
# ---------------------------------------------------------------------------


async def _const(value: Any) -> Any:
    return value


class _FakeCache:
    """记录写入的假缓存；`initial` 用于验证命中路径直接返回不再取数。"""

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self.stored: dict[str, Any] = dict(initial or {})
        self.written: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> Any:
        return self.stored.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.written.append((key, ttl))
        self.stored[key] = value


def _enriched(
    symbol: str,
    name: str,
    *,
    stock_id: int | None = None,
    latest_price: float | None = None,
    change_percent: float | None = None,
    **extra: Any,
) -> StockEnrichedOut:
    return StockEnrichedOut(
        id=abs(hash(symbol)) % 1_000_000 if stock_id is None else stock_id,
        exchange="Shanghai_Stocks",
        symbol=symbol,
        name=name,
        category="stock",
        asof=datetime(2026, 5, 8),
        latest_price=latest_price,
        change_percent=change_percent,
        **extra,
    )


def _patch_board_reads(
    monkeypatch: pytest.MonkeyPatch,
    *,
    members: list[tuple[str, str]],
    stats: dict[str, dict[str, Any]],
    enriched: list[StockEnrichedOut],
    snap: dict[str, Any],
    board_meta: dict[str, Any] | None = {"board_code": "BK0501", "board_name": "次新股"},
    membership_as_of: date | None = date(2026, 9, 20),
    daily_pct_chg: dict[int, float | None] | None = None,
    pct_calls: list[tuple[Any, list[int]]] | None = None,
) -> None:
    monkeypatch.setattr(concept_repo, "list_member_symbols", lambda db, code: _const(members))
    monkeypatch.setattr(concept_repo, "member_history_stats", lambda db, code: _const(stats))
    monkeypatch.setattr(concept_repo, "find_board", lambda db, code: _const(board_meta))
    monkeypatch.setattr(
        concept_repo, "board_membership_date", lambda db, code: _const(membership_as_of)
    )
    monkeypatch.setattr(
        market_service, "get_stocks_enriched_by_symbols", lambda db, s: _const(enriched)
    )
    monkeypatch.setattr(limit_up_service, "get_snapshot", lambda cache: _const(snap))

    async def _fake_daily_pct_chg(
        db: Any, as_of: date, stock_ids: list[int]
    ) -> dict[int, float | None]:
        if pct_calls is not None:
            pct_calls.append((as_of, list(stock_ids)))
        return dict(daily_pct_chg or {})

    monkeypatch.setattr(concept_repo, "daily_pct_chg_by_stock_ids", _fake_daily_pct_chg)


async def test_get_new_stock_board_merges_ladder_stats_and_membership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """端到端装配（无 DB）：梯队 → streak/is_lu、stats → never_broken/first_open、口径声明回传。"""
    pct_calls: list[tuple[Any, list[int]]] = []
    _patch_board_reads(
        monkeypatch,
        members=[("601091", "C沈鼓"), ("688837", "C信诺维")],
        stats={
            "601091": {"listed_trade_days": 2, "never_broken": True, "first_open": 13.0},
            "688837": {"listed_trade_days": 5, "never_broken": None, "first_open": 10.0},
        },
        enriched=[
            _enriched("601091", "C沈鼓", stock_id=101, latest_price=20.8, change_percent=20.0),
            _enriched("688837", "C信诺维", stock_id=102),  # 无行情
        ],
        snap={
            "as_of": date(2026, 9, 18),
            "echelons": [
                {"streak": 2, "label": "2连板", "stocks": [{"symbol": "601091", "streak": 2}]}
            ],
            "degraded_reason": None,
        },
        daily_pct_chg={101: 20.0},
        pct_calls=pct_calls,
    )
    cache = _FakeCache()

    body = await new_stock_service.get_new_stock_board(None, cache)

    assert set(body) == NEW_STOCKS_KEYS
    assert set(body["kpis"]) == KPI_KEYS
    assert body["board_code"] == "BK0501" and body["board_name"] == "次新股"
    assert body["source"] == "em_clist"
    assert body["as_of"] == "2026-09-18"
    assert body["membership_as_of"] == "2026-09-20", "该板的成分快照日必须回传"
    assert body["degraded_reason"] is None

    by_symbol = {i["symbol"]: i for i in body["items"]}
    assert set(by_symbol) == {"601091", "688837"}, "items 覆盖全部可解析成分"
    assert all(set(i) == ITEM_KEYS for i in body["items"])
    assert by_symbol["601091"]["streak"] == 2 and by_symbol["601091"]["is_lu"] is True
    assert by_symbol["601091"]["never_broken"] is True
    assert by_symbol["601091"]["above_first_open"] is True  # 20.8 >= 13.0
    assert by_symbol["688837"]["streak"] is None and by_symbol["688837"]["is_lu"] is False
    assert by_symbol["688837"]["never_broken"] is None, "不可判不得写成 False"
    assert by_symbol["688837"]["pct_chg"] is None and by_symbol["688837"]["close"] is None
    assert by_symbol["688837"]["above_first_open"] is None

    kpis = body["kpis"]
    assert (kpis["up_count"], kpis["unpriced_count"]) == (1, 1)
    assert kpis["up_count"] + kpis["flat_count"] + kpis["down_count"] + kpis[
        "unpriced_count"
    ] == len(body["items"])
    assert kpis["limit_up_count"] == 1 and kpis["unbroken_count"] == 1
    assert pct_calls == [(date(2026, 9, 18), [101, 102])], (
        "涨跌幅必须按 as_of + 全部 enriched stock_id 查 daily_quotes（含无当日行情的那只）"
    )
    assert cache.written == [
        (new_stock_service.NEW_STOCK_CACHE_KEY, new_stock_service.NEW_STOCK_TTL)
    ]


async def test_get_new_stock_board_buckets_use_as_of_pct_chg_not_enriched_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I2：`pct_chg` 取 as_of 的 `daily_quotes.pct_chg`——停牌的成分进 `unpriced`，不按陈旧值分档。

    601091 的 enriched `change_percent` 是 +20.0（推导值），as_of 官方 `pct_chg` 是 -3.5 →
    必须用 -3.5 且进 `down`；688837 在 as_of 停牌（查询无该 stock_id）→ `pct_chg=None` 进
    `unpriced`，即使 enriched 还挂着上一次行情的 +9.99 与 close。
    """
    pct_calls: list[tuple[Any, list[int]]] = []
    _patch_board_reads(
        monkeypatch,
        members=[("601091", "C沈鼓"), ("688837", "C信诺维")],
        stats={},
        enriched=[
            _enriched("601091", "C沈鼓", stock_id=101, latest_price=20.8, change_percent=20.0),
            _enriched("688837", "C信诺维", stock_id=102, latest_price=10.0, change_percent=9.99),
        ],
        snap={"as_of": date(2026, 9, 18), "echelons": [], "degraded_reason": None},
        daily_pct_chg={101: -3.5},
        pct_calls=pct_calls,
    )
    cache = _FakeCache()

    body = await new_stock_service.get_new_stock_board(None, cache)

    by_symbol = {i["symbol"]: i for i in body["items"]}
    assert by_symbol["601091"]["pct_chg"] == -3.5, "必须用 as_of 官方值，不是 +20.0 的推导值"
    assert by_symbol["688837"]["pct_chg"] is None, "as_of 停牌 → 缺失（不得沿用陈旧涨跌幅）"
    # close 仍取 enriched 最新行（诚实标注可能早于 as_of）
    assert by_symbol["688837"]["close"] == 10.0

    kpis = body["kpis"]
    assert (kpis["up_count"], kpis["flat_count"], kpis["down_count"]) == (0, 0, 1)
    assert kpis["unpriced_count"] == 1, "停牌成分必须落在 unpriced，不能进任何涨跌档"
    assert kpis["up_count"] + kpis["flat_count"] + kpis["down_count"] + kpis[
        "unpriced_count"
    ] == len(body["items"])
    assert kpis["avg_pct"] == pytest.approx(-3.5), "均值分母只有非空 pct_chg 家数"
    assert pct_calls == [(date(2026, 9, 18), [101, 102])]


async def test_get_new_stock_board_normalizes_cached_string_as_of(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """快照来自 Redis 时 `as_of` 是 JSON 字符串：必须归一成 `date` 再下传 `daily_quotes`。

    `get_snapshot` 的命中路径返回 `json.loads(...)`（`date` 被 `default=str` 序列化），
    若不归一则 `as_of.isoformat()` 崩、且字符串绑定 `trade_date` 在 asyncpg 侧报类型错。
    """
    pct_calls: list[tuple[Any, list[int]]] = []
    _patch_board_reads(
        monkeypatch,
        members=[("601091", "C沈鼓")],
        stats={},
        enriched=[_enriched("601091", "C沈鼓", stock_id=101, latest_price=20.8)],
        snap={"as_of": "2026-09-18", "echelons": [], "degraded_reason": None},
        daily_pct_chg={101: 1.5},
        pct_calls=pct_calls,
    )

    body = await new_stock_service.get_new_stock_board(None, _FakeCache())

    assert body["as_of"] == "2026-09-18"
    assert pct_calls == [(date(2026, 9, 18), [101])], "缓存快照的字符串 as_of 必须归一再查库"
    assert body["items"][0]["pct_chg"] == 1.5


async def test_get_new_stock_board_degraded_with_items_is_not_cached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I1：`degraded_reason` 非空时**即使 items 非空也不写缓存**（上游补齐后立即可见）。

    与 T6/T9 同款裁决：降级payload（如限价缺失使 is_lu 不可判）缓存 300s 会让前端同时看到
    自相矛盾的家数读数，正是计划风险表点名的"两个涨停家数"。
    """
    _patch_board_reads(
        monkeypatch,
        members=[("601091", "C沈鼓")],
        stats={"601091": {"listed_trade_days": 2, "never_broken": None, "first_open": 13.0}},
        enriched=[_enriched("601091", "C沈鼓", stock_id=101, latest_price=20.8)],
        snap={
            "as_of": date(2026, 9, 18),
            "echelons": [],
            "degraded_reason": "price_limits_missing",
        },
        daily_pct_chg={101: 2.0},
    )
    cache = _FakeCache()

    body = await new_stock_service.get_new_stock_board(None, cache)

    assert body["degraded_reason"] == "price_limits_missing"
    assert body["items"], "本条钉的正是「有数据但降级」——items 必须非空"
    assert cache.written == [], "降级响应（哪怕 items 非空）不得进缓存"


async def test_get_new_stock_board_no_members_degrades_and_does_not_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无成分行 → `no_members` 优先（哪怕快照报别的降级），且**不写缓存**（数据补齐后立即可见）。"""
    _patch_board_reads(
        monkeypatch,
        members=[],
        stats={},
        enriched=[],
        snap={"as_of": date(2026, 9, 18), "echelons": [], "degraded_reason": "no_quotes"},
    )
    cache = _FakeCache()

    body = await new_stock_service.get_new_stock_board(None, cache)

    assert body["degraded_reason"] == "no_members"
    assert body["items"] == []
    assert body["kpis"] == {
        "up_count": 0,
        "flat_count": 0,
        "down_count": 0,
        "unpriced_count": 0,
        "limit_up_count": 0,
        "unbroken_count": 0,
        "above_first_open_count": 0,
        "avg_pct": None,
    }
    assert cache.written == [], "空/降级响应不得进缓存"


async def test_get_new_stock_board_returns_cached_payload_without_reading_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缓存命中直接返回，不再打任何数据源（次新股卡每次进情绪 tab 都会请求）。"""

    def _explode(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("缓存命中不得再取数")

    monkeypatch.setattr(concept_repo, "list_member_symbols", _explode)
    monkeypatch.setattr(concept_repo, "member_history_stats", _explode)
    monkeypatch.setattr(limit_up_service, "get_snapshot", _explode)
    cached = {"as_of": "2026-09-18", "board_code": "BK0501", "items": [{"symbol": "601091"}]}
    cache = _FakeCache({new_stock_service.NEW_STOCK_CACHE_KEY: cached})

    assert await new_stock_service.get_new_stock_board(None, cache) is cached


# ---------------------------------------------------------------------------
# `-m e2e`：真库 + in-process ASGI app（不启 lifespan，故不触发 TuShare 回补）
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
async def _clear_new_stock_cache_and_dispose(
    request: pytest.FixtureRequest,
) -> AsyncGenerator[None, None]:
    """e2e 用例前清掉本命名空间缓存（挡住 dev API 留下的旧 as_of），结束释放 DB/Redis 连接池。

    默认门禁用例断言的正是缓存/取数语义，**不许连库**，按 marker 短路。`delete` 内部吞掉 Redis
    不可用（本地 6379/6380 未起时是 no-op），故本夹具不引入新的环境依赖。
    """
    is_e2e = request.node.get_closest_marker("e2e") is not None
    if is_e2e:
        client = CacheClient(await get_redis_pool())
        await client.delete(new_stock_service.NEW_STOCK_CACHE_KEY)
    try:
        yield
    finally:
        if is_e2e:
            await engine.dispose()
            await close_redis_pool()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _db_scalar(sql: str, params: dict[str, Any] | None = None) -> Any:
    async with async_session_factory() as db:
        return (await db.execute(text(sql), params or {})).scalar_one()


@pytest.mark.e2e
async def test_new_stocks_contract_matches_dev_db() -> None:
    """契约字段 + 与库内对拍 + 家数不变量（不依赖任何会变的当日数字）。"""
    member_count = int(
        await _db_scalar(
            "SELECT count(*) FROM concept_members WHERE board_code = :code",
            {"code": new_stock_service.NEW_STOCK_BOARD_CODE},
        )
    )
    unresolved = int(
        await _db_scalar(
            "SELECT count(*) FROM concept_members WHERE board_code = :code AND stock_id IS NULL",
            {"code": new_stock_service.NEW_STOCK_BOARD_CODE},
        )
    )
    membership_as_of = await _db_scalar(
        "SELECT max(last_seen_on) FROM concept_members WHERE board_code = :code",
        {"code": new_stock_service.NEW_STOCK_BOARD_CODE},
    )
    assert member_count > 0, "dev DB 缺 BK0501 成分（先跑 T4 采集）"
    assert unresolved == 0, "BK0501 全部成分应已解析（T0 名录刷新后）"

    async with _client() as client:
        resp = await client.get("/api/v1/new-stocks")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == NEW_STOCKS_KEYS
    assert set(body["kpis"]) == KPI_KEYS
    assert body["board_code"] == "BK0501" and body["board_name"] == "次新股"
    assert body["source"] == "em_clist"
    assert body["membership_as_of"] == membership_as_of.isoformat(), "历史口径声明必须回传"
    assert body["degraded_reason"] is None, "有数据时必须为 null"

    items = body["items"]
    assert len(items) == member_count - unresolved, "items = 全部可解析成分（未解析不参与统计）"
    for item in items:
        assert set(item) == ITEM_KEYS
        # 三态：True / False / None，**绝不为 0/1 整数**（缺失 ≠ 已开板，也不等于 0）
        assert item["never_broken"] is None or isinstance(item["never_broken"], bool)
        assert item["above_first_open"] is None or isinstance(item["above_first_open"], bool)
        assert item["is_lu"] is (item["streak"] is not None and item["streak"] >= 1), (
            "is_lu 必须与梯队 streak 派生一致（不得来自第二次涨停查询）"
        )
        if item["first_open"] is not None and item["close"] is not None:
            assert item["above_first_open"] == (item["close"] >= item["first_open"])

    kpis = body["kpis"]
    assert kpis["up_count"] + kpis["flat_count"] + kpis["down_count"] + kpis[
        "unpriced_count"
    ] == len(items), "四档家数之和必须等于表格行数（同一 items 口径）"
    pcts = [i["pct_chg"] for i in items if i["pct_chg"] is not None]
    if pcts:
        assert kpis["avg_pct"] == pytest.approx(sum(pcts) / len(pcts))
    else:
        assert kpis["avg_pct"] is None
    assert kpis["limit_up_count"] == sum(1 for i in items if i["is_lu"])
    assert kpis["unbroken_count"] == sum(1 for i in items if i["never_broken"] is True)
    assert kpis["above_first_open_count"] == sum(1 for i in items if i["above_first_open"] is True)
    # 未开板（上市以来每一行都涨停）⇒ **有当日行情时**今天也在涨停 → 必然在梯队里。停牌/无
    # 当日行情的成分（`pct_chg=null`）可能不在梯队，故不变量只在有行情的行上成立
    # （fix round 1 / M4：原断言在停牌成分出现时会误报）。
    unbroken_priced = sum(
        1 for i in items if i["never_broken"] is True and i["pct_chg"] is not None
    )
    assert unbroken_priced <= kpis["limit_up_count"], "有行情的未开板家数不可能超过当日涨停家数"


@pytest.mark.e2e
async def test_new_stocks_membership_as_of_is_the_board_own_max() -> None:
    """`membership_as_of` 必须是**该板**的 `max(last_seen_on)`，不是全表值（口径漂移最隐蔽处）。"""
    board_date = await _db_scalar(
        "SELECT max(last_seen_on) FROM concept_members WHERE board_code = :code",
        {"code": new_stock_service.NEW_STOCK_BOARD_CODE},
    )

    async with _client() as client:
        body = (await client.get("/api/v1/new-stocks")).json()

    # 与全表 max 的差异刻意不断言：BK0501 陈旧时两者本来就会不同，而响应必须给**本板**值
    # （T8 `board_membership_date` 的裁决同源）。
    assert body["membership_as_of"] == board_date.isoformat()
