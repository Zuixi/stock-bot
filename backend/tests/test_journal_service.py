"""spec §S9.3/§S9.4 —— 快照 / 到期结果 / 状态 / 统计（真 PG，`TEST_DATABASE_URL`）。

七条不变量（每条都对应 spec 的一句验收判据）：

1. **缺数即 null**：任一来源缺数 → 该字段 ``None`` + ``missing_reason``，**不得用 0**
   （snapshot 用例逐字段断言，含"没有这只股票"与"没有情绪行"两个来源）；
2. **快照 ≤5 条查询**：``build_snapshot`` 的查询数用 SQLAlchemy 事件计数器钉死；
   列表 + 三态计数也与条数无关（反 N+1）；
3. **快照不随行情变化**：创建后新插一根 K 线，重查条目快照仍是原值（重跑不改）；
4. **``due_on`` 固化**：= ``created_at::date + horizon_days``；
5. **PIT 对照（红/绿证明）**：把 ``snapshot.price`` 改成复盘日价格 → ``outcome_pct``
   **必须变**；用快照价才算得回原来那个数；
6. **三态划分**：未到期 ``open`` / 到期 ``due`` / 已复盘 ``reviewed``（互斥且穷尽）；
7. **统计自洽**：``by_attribution`` 合计 = ``reviewed_n``；``reviewed_n < 5`` 时占比
   为 ``None``（不是 0），``discipline_break_share`` 同理。

所有写入都在**回滚**事务里（Ruling 17：跑完库与跑前一致）；库不可达时 skip 而非红。
合成数据刻意用库里不存在的代码/行业（``T9000x`` / ``T9Lx``）与日期（2026-10-16 起），
避免与真实行情/申万成分互相污染。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.daily_basic import DailyBasicIndicator
from app.models.index_daily import IndexDaily
from app.models.industry_research import IndustryMetric
from app.models.journal import DecisionJournalEntry, DecisionJournalReview
from app.models.market_data import Announcement, DragonTigerEntry, StockPriceLimit
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.models.sw_industry import SwIndustryClass, SwIndustryMember
from app.schemas.journal import JournalEntryCreate
from app.services import journal_service as svc
from app.services.industry_registry import COMPOSITE_METRIC_KEY

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)
PREV_DAYS = 130
MAIN = "T90001"
MEMBER_A = "T90002"
MEMBER_B = "T90003"
L1, L2, L3 = "T9L1", "T9L2", "T9L3"
SYMBOLS = (MAIN, MEMBER_A, MEMBER_B)


#: 主标的的日线：收盘 100 + 0.1·i（单调上行 → ``new_high_low`` 必命中，MA20 可手算）
def main_close(i: int) -> float:
    return round(100.0 + 0.1 * i, 4)


MAIN_DAYS = [AS_OF - timedelta(days=PREV_DAYS - 1 - i) for i in range(PREV_DAYS)]
MAIN_CLOSES = [main_close(i) for i in range(PREV_DAYS)]
#: AS_OF 之后的四根 K 线（复盘窗口用）：故意最后一天拉回 121，让 outcome 有值可核
AFTER = [
    (date(2026, 10, 17), 115.0, 114.0, 116.0),
    (date(2026, 10, 19), 118.0, 117.0, 119.0),
    (date(2026, 10, 20), 112.0, 110.0, 118.0),
    (date(2026, 10, 21), 121.0, 111.0, 122.0),
]
#: 申万成员（含主标的）的起止收盘：行业平均 = (7.1745 + 5 + 0) / 3
MEMBER_START = {MEMBER_A: 50.0, MEMBER_B: 20.0}
MEMBER_END = {MEMBER_A: 52.5, MEMBER_B: 20.0}
DUE_ON = date(2026, 10, 21)


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


_PG_OK = _pg_reachable()

requires_pg = pytest.mark.skipif(
    not _PG_OK, reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 夹具
# ---------------------------------------------------------------------------


async def _with_rollback(factory: Any) -> Any:
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session, conn)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession) -> dict[str, int]:
    """合成底座：3 只股票 / 130 天行情 / 估值 / 涨停价 / 情绪 / 申万两级 / 公告 / 龙虎榜。"""
    await session.execute(
        insert(Stock),
        [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": symbol,
                "name": f"测试{symbol}",
                "category": "A股",
                "asof": datetime(2026, 10, 16, 18, 0),
            }
            for symbol in SYMBOLS
        ],
    )
    rows = (
        await session.execute(select(Stock.id, Stock.symbol).where(Stock.symbol.in_(SYMBOLS)))
    ).all()
    id_of = {str(symbol): int(stock_id) for stock_id, symbol in rows}

    await session.execute(
        insert(SwIndustryClass),
        [
            {"industry_code": L1, "level": 1, "industry_name": "测试一级", "parent_code": None},
            {"industry_code": L2, "level": 2, "industry_name": "测试二级", "parent_code": L1},
            {"industry_code": L3, "level": 3, "industry_name": "测试三级", "parent_code": L2},
        ],
    )
    await session.execute(
        insert(SwIndustryMember),
        [
            {
                "industry_code": L3,
                "stock_code": symbol,
                "symbol": symbol,
                "stock_name": symbol,
            }
            for symbol in SYMBOLS
        ],
    )

    quotes: list[dict[str, Any]] = []
    for i, day in enumerate(MAIN_DAYS):
        close = MAIN_CLOSES[i]
        prev = MAIN_CLOSES[i - 1] if i else close
        quotes.append(
            {
                "stock_id": id_of[MAIN],
                "trade_date": day,
                "open": prev,
                "high": round(close + 0.5, 4),
                "low": round(close - 0.5, 4),
                "close": close,
                "pre_close": prev,
                "amount": 1_000_000.0,
            }
        )
    prev = MAIN_CLOSES[-1]
    for day, close, low, high in AFTER:
        quotes.append(
            {
                "stock_id": id_of[MAIN],
                "trade_date": day,
                "open": prev,
                "high": high,
                "low": low,
                "close": close,
                "pre_close": prev,
                "amount": 1_000_000.0,
            }
        )
        prev = close
    for symbol, start, end in (
        (MEMBER_A, MEMBER_START[MEMBER_A], MEMBER_END[MEMBER_A]),
        (MEMBER_B, MEMBER_START[MEMBER_B], MEMBER_END[MEMBER_B]),
    ):
        quotes.append(
            {
                "stock_id": id_of[symbol],
                "trade_date": AS_OF,
                "open": start,
                "high": start,
                "low": start,
                "close": start,
                "pre_close": start,
                "amount": 100_000.0,
            }
        )
        quotes.append(
            {
                "stock_id": id_of[symbol],
                "trade_date": DUE_ON,
                "open": end,
                "high": end,
                "low": end,
                "close": end,
                "pre_close": start,
                "amount": 100_000.0,
            }
        )
    await session.execute(insert(DailyQuote), quotes)

    await session.execute(
        insert(DailyBasicIndicator),
        [
            {
                "stock_id": id_of[MAIN],
                "trade_date": day,
                "pe_ttm": round(20.0 + (i % 40) * 0.1, 4),
            }
            for i, day in enumerate(MAIN_DAYS)
        ],
    )
    await session.execute(
        insert(StockPriceLimit),
        [
            {
                "trade_date": AS_OF,
                "stock_id": id_of[MAIN],
                "ts_code": f"{MAIN}.SH",
                "pre_close": MAIN_CLOSES[-2],
                "up_limit": 124.0,
                "down_limit": 101.0,
            }
        ],
    )
    # 情绪综合指标：130 行（> 分位最小样本 126），单调上行 → 末值分位 = 100
    await session.execute(
        insert(IndustryMetric),
        [
            {
                "industry_key": "market",
                "stock_id": 0,
                "metric_key": COMPOSITE_METRIC_KEY,
                "source": "local_calc",
                "source_tier": "calc",
                "freq": "daily",
                "period": day,
                "value": round(30.0 + i * 0.1, 4),
                "published_at": datetime(2026, 10, 16, 19, 0),
            }
            for i, day in enumerate(MAIN_DAYS)
        ],
    )
    await session.execute(
        insert(Announcement),
        [
            {
                "announcement_id": f"T9-ANN-{uuid.uuid4().hex[:8]}",
                "sec_code": MAIN,
                "sec_name": f"测试{MAIN}",
                "title": "关于产能去化的进展公告",
                "announce_time": datetime(2026, 10, 15, 9, 0),
                "category": "event",
            }
        ],
    )
    await session.execute(
        insert(DragonTigerEntry),
        [
            {
                "trade_date": AS_OF,
                "ts_code": f"{MAIN}.SH",
                "name": f"测试{MAIN}",
                "net_amount": 12_000_000.0,
                "reason": "日涨幅偏离值达 7%",
            }
        ],
    )
    return id_of


def _snapshot_expected() -> dict[str, float]:
    last = MAIN_CLOSES[-1]
    ma20 = sum(MAIN_CLOSES[-svc.MA_DAYS :]) / svc.MA_DAYS
    return {
        "price": last,
        "pct_chg": round((last / MAIN_CLOSES[-2] - 1) * 100, 4),
        "ma20_dist_pct": round((last / ma20 - 1) * 100, 4),
    }


def _entry(*, user_id: uuid.UUID | None = None, **overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": user_id or uuid.uuid4(),
        "symbol": MAIN,
        "kind": "watch",
        "thesis": "猪价见底，产能去化加速",
        # 窗口 (AS_OF, DUE_ON] 内最低 110 → 阈值 111 必触发（见 AFTER 的第三根 K 线）
        "invalidation": {"kind": "price_below", "value": 111.0},
        "horizon_days": 7,
        "snapshot": {
            "version": svc.SNAPSHOT_VERSION,
            "as_of": AS_OF.isoformat(),
            "quote_as_of": AS_OF.isoformat(),
            "price": MAIN_CLOSES[-1],
            "sector_flow_state": {"code": L1, "name": "测试一级", "pct": 0.0, "rank": 0.0},
        },
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


async def _count_queries(
    conn: Any, coro_factory: Any, *, statements: list[str] | None = None
) -> tuple[Any, int]:
    """跑一次并统计 SQL 条数（``statements`` 传入时逐条收集，用于"只读"断言）。"""
    counter = {"n": 0}

    def _count(_conn: Any, _cursor: Any, statement: str, *_args: Any, **_kwargs: Any) -> None:
        counter["n"] += 1
        if statements is not None:
            statements.append(statement)

    event.listen(conn.sync_engine, "before_cursor_execute", _count)
    try:
        result = await coro_factory()
    finally:
        event.remove(conn.sync_engine, "before_cursor_execute", _count)
    return result, counter["n"]


# ---------------------------------------------------------------------------
# 快照摘要（列表行的 1 行文案：spec §S9.1）
# ---------------------------------------------------------------------------


def test_snapshot_schema_is_fixed() -> None:
    """快照的七个字段 + 八个元数据键逐字固定（多一个少一个都必须过这里和 spec）。"""
    assert svc.SNAPSHOT_FIELDS == (
        "price",
        "pct_chg",
        "ma20_dist_pct",
        "sentiment_percentile",
        "sector_flow_state",
        "events",
        "valuation_pct",
    )
    assert svc.SNAPSHOT_META_FIELDS == (
        "version",
        "as_of",
        "quote_as_of",
        "symbol",
        "missing_reason",
        # spec §S9.7.3（s9.r2）：基准口径在创建时固化
        "bench_spec",
        "bench_basis_note",
        # spec §S9.6.4（s9.r3）：创建那一刻的同类历史
        "peer_history",
    )
    assert svc.SNAPSHOT_VERSION == "s9.r3"


def test_snapshot_line_joins_only_the_fields_that_have_values() -> None:
    """缺数**不补 0**、也不编造：没有值的字段整段消失；一段都没有时明说没取到快照。"""
    assert (
        svc.snapshot_line(
            {
                "price": 12.34,
                "pct_chg": -1.2,
                "sentiment_percentile": 63.4,
                "events": [{"detector": "drop_sharp", "headline": "跌得急"}],
            }
        )
        == "价格 12.34 元 · 当日 -1.20% · 情绪分位 63 · 当日事件 1 条"
    )
    # 只有价格：其余字段缺数 → 只写价格（不出现 "当日 +0.00%" 这种假值）
    assert svc.snapshot_line({"price": 9.0, "pct_chg": None}) == "价格 9.00 元"
    for empty in ({}, None, {"price": None, "pct_chg": None, "events": []}):
        assert svc.snapshot_line(empty) == "创建时没有取到快照"


# ---------------------------------------------------------------------------
# §S9.3 快照：字段 / 缺数 / 查询数 / 不可变
# ---------------------------------------------------------------------------


@requires_pg
async def test_snapshot_fields_come_from_the_five_data_faces() -> None:
    """有数据时七个字段全部落进快照（字段名由 schema 固定，新增必须同时改 spec 与测试）。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await _seed(session)
        return await svc.build_snapshot(session, symbol=MAIN, as_of=AS_OF)

    snapshot = await _with_rollback(_go)
    expected = _snapshot_expected()
    # 字段名由 schema 固定（spec §S9.3 门禁）：**新增字段必须同时改 spec 与本断言**
    assert set(snapshot) == set(svc.SNAPSHOT_FIELDS) | set(svc.SNAPSHOT_META_FIELDS)
    assert snapshot["version"] == svc.SNAPSHOT_VERSION
    assert snapshot["quote_as_of"] == AS_OF.isoformat()
    assert snapshot["price"] == pytest.approx(expected["price"], abs=1e-4)
    assert snapshot["pct_chg"] == pytest.approx(expected["pct_chg"], abs=1e-4)
    assert snapshot["ma20_dist_pct"] == pytest.approx(expected["ma20_dist_pct"], abs=1e-4)
    assert snapshot["valuation_pct"] is not None
    assert 0.0 <= snapshot["valuation_pct"] <= 100.0
    # 130 行单调上行的情绪序列 → 末值是窗口最大值 → 分位 100（nearest-rank 口径）
    assert snapshot["sentiment_percentile"] == pytest.approx(100.0, abs=1e-6)
    sector = snapshot["sector_flow_state"]
    assert sector["code"] == L1
    assert sector["name"] == "测试一级"
    assert isinstance(sector["pct"], float)
    assert isinstance(sector["rank"], float)
    # 事件复用 S8 探测器：公告 / 龙虎榜 / 20 日新高 三条都该命中
    detectors = {event["detector"] for event in snapshot["events"]}
    assert detectors == {"new_announcement", "dragon_tiger", "new_high_low"}
    assert all(event["headline"] for event in snapshot["events"])
    assert snapshot["missing_reason"] == {}


@requires_pg
async def test_snapshot_missing_quote_is_null_with_reason_not_zero() -> None:
    """没有这只股票的行情 → ``price is None`` + 原因；**绝不是 0**（spec §S9.3）。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await _seed(session)
        return await svc.build_snapshot(session, symbol="T99999", as_of=AS_OF)

    snapshot = await _with_rollback(_go)
    assert snapshot["price"] is None, "缺数必须是 null，用 0 会被读成'跌了 0%'"
    assert snapshot["price"] != 0
    assert snapshot["pct_chg"] is None
    assert snapshot["ma20_dist_pct"] is None
    assert snapshot["valuation_pct"] is None
    assert snapshot["sector_flow_state"] is None
    assert snapshot["events"] == []
    assert snapshot["missing_reason"]["price"] == "no_daily_quote"
    assert snapshot["missing_reason"]["sector_flow_state"] == "no_sector_data"
    assert "valuation_pct" in snapshot["missing_reason"]
    # 情绪是市场级指标：与有没有这只股票无关，仍应算出来
    assert snapshot["sentiment_percentile"] is not None


@requires_pg
async def test_snapshot_missing_sentiment_row_is_null_with_reason() -> None:
    """情绪行不存在（as_of 早于所有行）→ ``None`` + ``no_market_sentiment_row``。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await _seed(session)
        return await svc.build_snapshot(session, symbol=None, as_of=date(2020, 1, 2))

    snapshot = await _with_rollback(_go)
    assert snapshot["sentiment_percentile"] is None
    assert snapshot["missing_reason"]["sentiment_percentile"] == "no_market_sentiment_row"
    # 市场级判断：价格类字段全部 null + 原因（不是 0，也不是假装"市场没动"）
    assert snapshot["price"] is None
    assert snapshot["missing_reason"]["price"] == "market_level_no_symbol"
    assert snapshot["events"] == []


@requires_pg
async def test_snapshot_sentiment_percentile_needs_min_samples() -> None:
    """情绪样本不足最小分位样本 → ``None`` + ``percentile_below_min_samples``（不给假分位）。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await _seed(session)
        # 只留最近 10 行（< 126）：窗口内样本不足
        return await svc.build_snapshot(session, symbol=None, as_of=MAIN_DAYS[9])

    snapshot = await _with_rollback(_go)
    assert snapshot["sentiment_percentile"] is None
    assert snapshot["missing_reason"]["sentiment_percentile"] == "percentile_below_min_samples"


@requires_pg
async def test_build_snapshot_uses_at_most_five_queries() -> None:
    """spec §S9.3：只读 + **≤5 条查询**（五个数据面各一条，各自内部批量）。"""

    async def _go(session: AsyncSession, conn: Any) -> tuple[dict[str, Any], int, list[str]]:
        await _seed(session)
        statements: list[str] = []
        snapshot, count = await _count_queries(
            conn,
            lambda: svc.build_snapshot(session, symbol=MAIN, as_of=AS_OF),
            statements=statements,
        )
        return snapshot, count, statements

    snapshot, count, statements = await _with_rollback(_go)
    assert snapshot["price"] is not None
    assert count <= 5, f"快照查询数 {count} 超过 spec 的 5 条上限"
    assert count >= 4, f"只查了 {count} 条，快照字段大概没取全"
    # 只读：五条查询全是 SELECT/WITH，没有一条 INSERT/UPDATE/DELETE
    for statement in statements:
        assert statement.lstrip().upper().startswith(("SELECT", "WITH")), (
            f"build_snapshot 必须是只读的，出现了写语句：{statement[:60]}"
        )


@requires_pg
async def test_list_and_counts_query_count_is_independent_of_row_count() -> None:
    """反 N+1：3 条与 12 条的查询数相同（列表 1 条 + 三态计数 1 条）。"""

    async def _go(session: AsyncSession, conn: Any) -> tuple[int, int]:
        user = uuid.uuid4()
        for _ in range(3):
            session.add(_entry(user_id=user, due_on=DUE_ON))
        await session.flush()

        async def _small() -> None:
            await svc.list_entries(session, user, status=None)
            await svc.status_counts(session, user)

        _, small = await _count_queries(conn, _small)
        for _ in range(9):
            session.add(_entry(user_id=user, due_on=DUE_ON))
        await session.flush()

        async def _large() -> None:
            await svc.list_entries(session, user, status=None)
            await svc.status_counts(session, user)

        _, large = await _count_queries(conn, _large)
        return small, large

    small, large = await _with_rollback(_go)
    assert small == large, f"查询数随条数增长（N+1）：3 条 {small} → 12 条 {large}"
    assert small <= 2


@requires_pg
async def test_create_entry_freezes_snapshot_and_due_on() -> None:
    """创建时快照落库 + ``due_on = created_at::date + horizon_days``；行情再动也不改快照。"""

    async def _go(session: AsyncSession, _conn: Any) -> tuple[DecisionJournalEntry, float, float]:
        id_of = await _seed(session)
        payload = JournalEntryCreate(
            symbol=MAIN,
            kind="buy",
            thesis="猪价见底，产能去化加速",
            invalidation={"kind": "price_below", "value": 90.0},
            horizon_days=30,
        )
        now = datetime(2026, 10, 16, 15, 30)
        entry = await svc.create_entry(session, uuid.uuid4(), payload, now=now)
        frozen = float(entry.snapshot["price"])
        assert entry.due_on == AS_OF + timedelta(days=30)
        assert entry.created_at.date() == AS_OF
        # 行情真的动了：插一根远高于快照价的新 K 线（用 AFTER 之外的日期，避免与底料撞键）
        moved_on = date(2026, 10, 23)
        await session.execute(
            insert(DailyQuote),
            [
                {
                    "stock_id": id_of[MAIN],
                    "trade_date": moved_on,
                    "open": 900.0,
                    "high": 999.0,
                    "low": 899.0,
                    "close": 999.0,
                    "pre_close": frozen,
                    "amount": 9_000_000.0,
                }
            ],
        )
        await session.flush()
        fresh = await svc.get_entry(session, entry.user_id, entry.id)
        rebuilt = await svc.build_snapshot(session, symbol=MAIN, as_of=moved_on)
        return fresh.entry, frozen, float(rebuilt["price"])

    entry, frozen, rebuilt = await _with_rollback(_go)
    assert float(entry.snapshot["price"]) == pytest.approx(frozen, abs=1e-6), (
        "快照必须是创建时的值：重跑/行情变化不得改写已落库的快照"
    )
    assert rebuilt != frozen, "构造失败：新 K 线没生效，这条用例证明不了'不可变'"


# ---------------------------------------------------------------------------
# §S9.4 结果：PIT 对照（红/绿证明）
# ---------------------------------------------------------------------------


@requires_pg
async def test_evaluate_entry_uses_snapshot_price_not_review_day_price() -> None:
    """**PIT 对照**：快照价 → 7.17%；把快照价换成复盘日价 → 0% ⇒ 用的是快照价。

    这条用例的价值在"能变红"：如果实现偷偷用复盘日收盘当起始价，
    :func:`expected_from_snapshot` 那半边会立刻失败。
    """

    async def _go(session: AsyncSession, _conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed(session)
        entry = _entry()
        session.add(entry)
        await session.flush()
        from_snapshot = await svc.evaluate_entry(session, entry)
        # 反向对照：把快照价改成复盘日价格，结果必须变
        entry.snapshot = dict(entry.snapshot)
        entry.snapshot["price"] = AFTER[-1][1]
        from_review_day = await svc.evaluate_entry(session, entry)
        return from_snapshot, from_review_day

    from_snapshot, from_review_day = await _with_rollback(_go)
    start = MAIN_CLOSES[-1]
    end = AFTER[-1][1]
    assert from_snapshot["outcome_pct"] == pytest.approx(
        round((end / start - 1) * 100, 4), abs=1e-4
    )
    # 起始价换成复盘日价 → outcome 必然为 0（"变化"本身就是证明）
    assert from_review_day["outcome_pct"] == pytest.approx(0.0, abs=1e-6)
    assert from_review_day["outcome_pct"] != from_snapshot["outcome_pct"]
    # 基准 = 申万一级成员等权平均（主标的 + 两个成员的起止收盘）
    bench = round(
        (
            (end / start - 1) * 100
            + (MEMBER_END[MEMBER_A] / MEMBER_START[MEMBER_A] - 1) * 100
            + (MEMBER_END[MEMBER_B] / MEMBER_START[MEMBER_B] - 1) * 100
        )
        / 3,
        4,
    )
    assert from_snapshot["bench_pct"] == pytest.approx(bench, abs=1e-3)
    # 期末口径如实标出"数据截至哪天"（不是把 due_on 当数据日）
    assert from_snapshot["price_as_of"] == DUE_ON.isoformat()
    assert from_snapshot["excess_pct"] == pytest.approx(
        from_snapshot["outcome_pct"] - from_snapshot["bench_pct"], abs=1e-4
    )
    # 窗口 (快照日, due_on] 内最低 110 → 阈值 111 触发；阈值 90 不触发
    assert from_snapshot["invalidation_hit"] is True


@requires_pg
async def test_invalidation_hit_price_above_and_date_only() -> None:
    """三个 kind 的判定口径：``price_above`` 看窗口最高；``date_only`` 到期即 true。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await _seed(session)
        results = {}
        for name, invalidation in (
            ("above_hit", {"kind": "price_above", "value": 121.5}),
            ("above_miss", {"kind": "price_above", "value": 999.0}),
            ("below_miss", {"kind": "price_below", "value": 50.0}),
            ("date_only", {"kind": "date_only", "value": DUE_ON.isoformat()}),
        ):
            entry = _entry(invalidation=invalidation)
            session.add(entry)
            await session.flush()
            results[name] = await svc.evaluate_entry(session, entry, as_of=DUE_ON)
        return results

    results = await _with_rollback(_go)
    assert results["above_hit"]["invalidation_hit"] is True  # 窗口最高 122 ≥ 121.5
    assert results["above_miss"]["invalidation_hit"] is False
    assert results["below_miss"]["invalidation_hit"] is False
    assert results["date_only"]["invalidation_hit"] is True


@requires_pg
async def test_market_level_entry_uses_hs300_for_outcome_and_bench() -> None:
    """无 symbol 的市场级判断：outcome 与 bench 都是沪深300 ⇒ ``excess_pct = 0``。"""

    async def _go(session: AsyncSession, _conn: Any) -> dict[str, Any]:
        await session.execute(
            insert(IndexDaily),
            [
                {
                    "ts_code": svc.HS300_TS_CODE,
                    "trade_date": AS_OF,
                    "close": 4000.0,
                },
                {
                    "ts_code": svc.HS300_TS_CODE,
                    "trade_date": DUE_ON,
                    "close": 4200.0,
                },
            ],
        )
        entry = _entry(
            symbol=None,
            snapshot={
                "version": svc.SNAPSHOT_VERSION,
                "as_of": AS_OF.isoformat(),
                "quote_as_of": None,
                "price": None,
                "missing_reason": {"price": "market_level_no_symbol"},
            },
            invalidation={"kind": "date_only", "value": DUE_ON.isoformat()},
        )
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["outcome_pct"] == pytest.approx(5.0, abs=1e-4)
    assert result["bench_pct"] == pytest.approx(5.0, abs=1e-4)
    assert result["excess_pct"] == pytest.approx(0.0, abs=1e-6)
    assert result["invalidation_hit"] is True


@requires_pg
async def test_evaluate_entries_batch_is_constant_queries_and_same_numbers() -> None:
    """批量算结果：**查询数与条目数无关**（反 N+1），且与单条口径一致。"""

    async def _go(
        session: AsyncSession, conn: Any
    ) -> tuple[dict[int, dict[str, Any]], dict[int, dict[str, Any]], int, int]:
        await _seed(session)
        entries = [_entry(due_on=DUE_ON) for _ in range(3)]
        session.add_all(entries)
        await session.flush()
        single, q_single = await _count_queries(
            conn, lambda: svc.evaluate_entries(session, entries[:1])
        )
        batch, q_batch = await _count_queries(conn, lambda: svc.evaluate_entries(session, entries))
        return single, batch, q_single, q_batch

    single, batch, q_single, q_batch = await _with_rollback(_go)
    assert q_single == q_batch, f"查询数随条目数增长（N+1）：1 条 {q_single} → 3 条 {q_batch}"
    assert q_batch <= 3
    expected = round((AFTER[-1][1] / MAIN_CLOSES[-1] - 1) * 100, 4)
    assert len(batch) == 3
    for result in batch.values():
        assert result["outcome_pct"] == pytest.approx(expected, abs=1e-4)
    # 单条与批量的口径必须逐字段一致（同一套 _assemble，不允许两处漂移）
    ((single_id, single_result),) = single.items()
    assert single_result == batch[single_id]


# ---------------------------------------------------------------------------
# 三态 / 复盘 / 统计
# ---------------------------------------------------------------------------


@requires_pg
async def test_status_partitions_are_exclusive_and_complete() -> None:
    """``open`` 未到期 / ``due`` 到期待复盘 / ``reviewed`` 已复盘（三态互斥、计数自洽）。"""

    async def _go(session: AsyncSession, _conn: Any) -> tuple[dict[str, int], dict[str, int]]:
        user = uuid.uuid4()
        today = DUE_ON + timedelta(days=1)
        reviewed = _entry(user_id=user, due_on=DUE_ON - timedelta(days=3))
        due = _entry(user_id=user, due_on=DUE_ON)
        opened = _entry(user_id=user, due_on=today + timedelta(days=5))
        session.add_all([reviewed, due, opened])
        await session.flush()
        session.add(
            DecisionJournalReview(
                entry_id=reviewed.id,
                outcome_pct=1.0,
                bench_pct=0.5,
                excess_pct=0.5,
                invalidation_hit=False,
                verdict="luck",
            )
        )
        await session.flush()
        counts = await svc.status_counts(session, user, today=today)
        buckets = {
            status: len(await svc.list_entries(session, user, status=status, today=today))
            for status in svc.STATUSES
        }
        return counts, buckets

    counts, buckets = await _with_rollback(_go)
    assert counts == {"total": 3, "open": 1, "due": 1, "reviewed": 1}
    assert buckets == {"open": 1, "due": 1, "reviewed": 1}
    assert sum(buckets.values()) == counts["total"]


@requires_pg
async def test_review_writes_once_and_rejects_second_review() -> None:
    """一次复盘一条：第二次 → ``AlreadyReviewed``（不是静默覆盖）。"""

    async def _go(session: AsyncSession, _conn: Any) -> tuple[DecisionJournalReview, str]:
        await _seed(session)
        user = uuid.uuid4()
        entry = _entry(user_id=user)
        session.add(entry)
        await session.flush()
        review = await svc.review_entry(
            session,
            user,
            entry.id,
            verdict="discipline_break",
            note="追高",
            now=datetime(2026, 10, 21, 16, 0),
        )
        with pytest.raises(svc.AlreadyReviewed):
            await svc.review_entry(
                session, user, entry.id, verdict="luck", now=datetime(2026, 10, 21, 17, 0)
            )
        return review, entry.thesis

    review, _ = await _with_rollback(_go)
    assert review.verdict == "discipline_break"
    assert review.note == "追高"
    assert review.invalidation_hit is True
    assert float(review.excess_pct) == pytest.approx(
        float(review.outcome_pct) - float(review.bench_pct), abs=1e-4
    )


@requires_pg
async def test_review_rejects_not_due_and_foreign_entries() -> None:
    """未到期 422（``NotDueYet``）· 非本人 404（``EntryNotFound``）· 越界 verdict 拒绝。"""

    async def _go(session: AsyncSession, _conn: Any) -> tuple[bool, bool, bool]:
        await _seed(session)
        user, other = uuid.uuid4(), uuid.uuid4()
        future = _entry(user_id=user, due_on=date(2026, 12, 1))
        session.add(future)
        await session.flush()
        with pytest.raises(svc.NotDueYet):
            await svc.review_entry(
                session, user, future.id, verdict="luck", now=datetime(2026, 10, 21, 16, 0)
            )
        with pytest.raises(svc.EntryNotFound):
            await svc.review_entry(
                session, other, future.id, verdict="luck", now=datetime(2026, 12, 2, 16, 0)
            )
        with pytest.raises(ValueError):
            await svc.review_entry(
                session, user, future.id, verdict="gambling", now=datetime(2026, 12, 2, 16, 0)
            )
        with pytest.raises(svc.EntryNotFound):
            await svc.get_entry(session, other, future.id)
        return True, True, True

    assert await _with_rollback(_go) == (True, True, True)


@requires_pg
async def test_review_rejects_when_evaluation_is_not_ready() -> None:
    """结果算不出来（无行情）→ 拒绝写入，**不得用 0 兜底**。"""

    async def _go(session: AsyncSession, _conn: Any) -> None:
        user = uuid.uuid4()
        entry = _entry(
            user_id=user,
            symbol="T99999",
            snapshot={
                "version": svc.SNAPSHOT_VERSION,
                "as_of": AS_OF.isoformat(),
                "quote_as_of": None,
                "price": None,
                "missing_reason": {"price": "no_daily_quote"},
            },
            invalidation={"kind": "date_only", "value": DUE_ON.isoformat()},
        )
        session.add(entry)
        await session.flush()
        with pytest.raises(svc.EvaluationNotReady):
            await svc.review_entry(
                session, user, entry.id, verdict="luck", now=datetime(2026, 10, 21, 16, 0)
            )

    await _with_rollback(_go)


@requires_pg
async def test_stats_is_arithmetically_self_consistent() -> None:
    """``by_attribution`` 合计 = ``reviewed_n``；``reviewed_n < 5`` 时占比为 ``None``。"""

    async def _go(session: AsyncSession, _conn: Any) -> tuple[dict[str, Any], dict[str, Any]]:
        big = uuid.uuid4()
        verdicts = ["discipline_break", "discipline_break", "luck", "timing_wrong", "thesis_wrong"]
        for i, verdict in enumerate(verdicts):
            entry = _entry(user_id=big, due_on=DUE_ON)
            session.add(entry)
            await session.flush()
            session.add(
                DecisionJournalReview(
                    entry_id=entry.id,
                    outcome_pct=float(i + 1),
                    bench_pct=1.0,
                    excess_pct=float(i),
                    invalidation_hit=False,
                    verdict=verdict,
                )
            )
        session.add(_entry(user_id=big, due_on=DUE_ON))  # 一条未复盘 → n=6
        small = uuid.uuid4()
        for verdict in ("luck", "thesis_wrong"):
            entry = _entry(user_id=small, due_on=DUE_ON)
            session.add(entry)
            await session.flush()
            session.add(
                DecisionJournalReview(
                    entry_id=entry.id,
                    outcome_pct=1.0,
                    bench_pct=1.0,
                    excess_pct=0.0,
                    invalidation_hit=False,
                    verdict=verdict,
                )
            )
        await session.flush()
        return await svc.stats(session, big), await svc.stats(session, small)

    big, small = await _with_rollback(_go)
    assert big["n"] == 6
    assert big["reviewed_n"] == 5
    assert big["pending_n"] == 1
    assert sum(big["by_attribution"].values()) == big["reviewed_n"]
    assert big["by_attribution"]["discipline_break"] == 2
    assert big["discipline_break_share"] == pytest.approx(0.4)
    assert big["sample_sufficient"] is True
    assert big["avg_excess_pct"] == pytest.approx(2.0)  # (0+1+2+3+4)/5
    # by_verdict 必须是**数值**映射（前端拿它算 count/reviewed_n；对象会渲染崩）
    assert big["by_verdict"]["luck"] == 1
    assert big["by_verdict"]["thesis_wrong"] == 1
    assert all(isinstance(value, int) for value in big["by_verdict"].values())
    assert sum(big["by_verdict"].values()) == big["reviewed_n"]

    assert small["reviewed_n"] == 2
    assert sum(small["by_attribution"].values()) == small["reviewed_n"]
    assert small["sample_sufficient"] is False
    assert small["discipline_break_share"] is None, "无样本的占比必须是 null，不是 0"
    assert small["by_verdict"]["luck"] == 1
    # 均值不是"占比"：样本不足仍可给（这里两条 excess 都是 0）
    assert small["avg_excess_pct"] == pytest.approx(0.0)
