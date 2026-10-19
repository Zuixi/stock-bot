"""S9 深度优化后端（`.superpowers/sdd/journal-deep/contract.md` §1 / §2）。

这一组钉两件事，每条对应契约里的一句验收判据：

1. **财报窗口**（契约 §1）：``evaluation.earnings_in_window`` 是**三态**而不是布尔
   （``true`` 窗口内有披露 / ``false`` 有财报数据但不在窗口内 / ``null`` 该股没有任何
   财报记录），``earnings_note`` 是人话且只在 ``true`` 时非空；PIT：只用
   ``ann_date <= due_on`` 的记录判断，窗口是 **(快照日, due_on]**（左开右闭）。
   数据源核实：``financial_report_versions`` 自带 ``stock_id`` + ``ann_date``
   （真库 330,362 行、5,587 只股票，全部有 ``ann_date``），所以财报行直接按
   ``stock_id`` 关联到 ``stocks``，不需要绕 ``income_statement_facts``。
2. **归因自洽**（契约 §2）：五类判定逐类可核（``discipline_break`` **恒自洽**、
   ``thesis_wrong`` + 正超额 = **不自洽**），``n < 5`` 时 ``share/ci_low/ci_high``
   一律 ``null``，``n >= 5`` 时与 ``wilson_interval`` 逐值一致（**复用**既有实现）。

数据纪律（沿用 Phase B2 的 Ruling 17）：股票行用**测试库里真实存在的**代码
（``600519`` / ``600036``），财报行在**回滚事务**里构造（测试库
``financial_report_versions`` 是空的；真库的核实结果写在 ``backend-report.md``），
所有写入随事务回滚，跑完库与跑前一致；库不可达时 skip 而非红。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy import delete, event, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.financial import FinancialReportVersion
from app.models.index_daily import IndexDaily
from app.models.journal import DecisionJournalEntry, DecisionJournalReview
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)  # 快照日（区间左端点，**不含**）
DAY_1 = date(2026, 10, 19)
DUE_ON = date(2026, 10, 21)  # 到期日（区间右端点，**含**）
PRICE = 100.0
HS300_START, HS300_END = 4000.0, 4040.0

#: 真库（stocks 表）里存在的两只标的：①用来构造"有财报"②"没有财报"（空纪律）
WITH_REPORTS = "600519"
WITHOUT_REPORTS = "600036"


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(SYNC_URL)
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason=f"PostgreSQL unavailable at {SYNC_URL} (set TEST_DATABASE_URL)"
)


# ---------------------------------------------------------------------------
# 夹具（回滚事务）
# ---------------------------------------------------------------------------


async def _with_rollback(factory: Any) -> Any:
    engine = create_async_engine(ASYNC_URL)
    try:
        async with engine.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn)
            try:
                return await factory(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await engine.dispose()


async def _with_rollback_conn(factory: Any) -> Any:
    """和 ``_with_rollback`` 一样回滚，但把连接一起交给用例（数 SQL 用）。"""
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


async def _count_queries(conn: Any, coro_factory: Any) -> tuple[Any, int, list[str]]:
    counter = {"n": 0}
    statements: list[str] = []

    def _count(_conn: Any, _cursor: Any, statement: str, *_a: Any, **_k: Any) -> None:
        counter["n"] += 1
        statements.append(statement)

    event.listen(conn.sync_engine, "before_cursor_execute", _count)
    try:
        result = await coro_factory()
    finally:
        event.remove(conn.sync_engine, "before_cursor_execute", _count)
    return result, counter["n"], statements


async def _stock_id(session: AsyncSession, symbol: str) -> int:
    """取股票 id；**库里没有就建一条**（CI 是空库，只跑 `alembic upgrade head`）。

    过去这里直接 `scalar_one()`，等于依赖「作者本地名录里恰好有这只票」——分支一推 CI
    就 36 连红（`NoResultFound`）。建行与用例同处一个被回滚的事务，不污染其它用例；
    已有行时行为与原来完全一致。
    """
    found = (
        await session.execute(select(Stock.id).where(Stock.symbol == symbol))
    ).scalar_one_or_none()
    if found is not None:
        return int(found)
    await session.execute(
        insert(Stock).values(
            exchange="Shanghai_Stocks",
            symbol=symbol,
            name=f"测试{symbol}",
            category="A股",
            asof=datetime(2026, 10, 16, 18, 0),
        )
    )
    return int((await session.execute(select(Stock.id).where(Stock.symbol == symbol))).scalar_one())


async def _clear_reports(session: AsyncSession, stock_id: int) -> None:
    """清掉该标的在**本事务内**可见的财报行（真库是空的，这里只为确定性）。"""
    await session.execute(
        delete(FinancialReportVersion).where(FinancialReportVersion.stock_id == stock_id)
    )


async def _seed_quotes(session: AsyncSession) -> None:
    """两只标的的日线（入场 100 → 到期 110）+ 沪深300 起止（让结果"就绪"）。"""
    ids = {symbol: await _stock_id(session, symbol) for symbol in (WITH_REPORTS, WITHOUT_REPORTS)}
    await session.execute(
        insert(DailyQuote),
        [
            _quote(stock_id, day, close=close)
            for stock_id in ids.values()
            for day, close in ((AS_OF, PRICE), (DAY_1, 101.0), (DUE_ON, 110.0))
        ],
    )
    await session.execute(
        insert(IndexDaily),
        [
            {"ts_code": svc.HS300_TS_CODE, "trade_date": AS_OF, "close": HS300_START},
            {"ts_code": svc.HS300_TS_CODE, "trade_date": DUE_ON, "close": HS300_END},
        ],
    )
    await session.flush()


def _quote(stock_id: int, day: date, *, close: float) -> dict[str, Any]:
    return {
        "stock_id": stock_id,
        "trade_date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": PRICE,
        "amount": 1_000_000.0,
    }


async def _add_report(
    session: AsyncSession,
    symbol: str,
    *,
    ann_date: date | None,
    end_date: date,
    report_type: str = "H1",
) -> None:
    """写一行财报版本（``stock_id`` 是它自带的列，直接对应 ``stocks.id``）。"""
    session.add(
        FinancialReportVersion(
            stock_id=await _stock_id(session, symbol),
            ts_code=None,
            end_date=end_date,
            report_type=report_type,
            ann_date=ann_date,
            comp_type="1",
            source="tushare",
            as_of=datetime(2026, 10, 16, 18, 0),
        )
    )
    await session.flush()


def _snapshot(symbol: str | None, price: float | None = PRICE) -> dict[str, Any]:
    return {
        "version": "s9.r1",
        "as_of": AS_OF.isoformat(),
        "quote_as_of": AS_OF.isoformat(),
        "symbol": symbol,
        "price": price,
        "sector_flow_state": None,
        "missing_reason": {},
    }


def _entry(symbol: str | None = WITH_REPORTS, **overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "symbol": symbol,
        "kind": "watch",
        "bet_type": "direction" if symbol else "market",
        "position_size": "none",
        "thesis": "猪价见底，产能去化加速",
        "invalidation": {"kind": "date_only", "value": DUE_ON.isoformat()},
        "horizon_days": 7,
        "snapshot": _snapshot(symbol),
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


# ---------------------------------------------------------------------------
# 契约 §1 财报窗口：三态 + 窗口边界 + PIT + 人话
# ---------------------------------------------------------------------------


@requires_pg
async def test_earnings_in_window_true_with_human_note() -> None:
    """窗口内有 1 次披露（10-19）→ ``true`` + 契约 §1 的人话说明。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITH_REPORTS))
        await _add_report(session, WITH_REPORTS, ann_date=DAY_1, end_date=date(2026, 9, 30))
        entry = _entry()
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["earnings_in_window"] is True
    assert result["earnings_note"] == ("窗口内含 1 次财报披露（10-19），结果可能被财报事件主导")
    assert "earnings" not in result["missing_reason"], "有数据就不该有缺数原因"
    # 三态里的另外两个值必须**不是**这个值（防"用 False 兜底 true"）
    assert result["earnings_in_window"] is not False
    assert result["earnings_in_window"] is not None


@requires_pg
async def test_earnings_in_window_counts_distinct_announcement_days() -> None:
    """两次公告日（10-19 / 10-21）+ 同日多份报表 → "2 次披露"，同日只数一次。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITH_REPORTS))
        await _add_report(session, WITH_REPORTS, ann_date=DAY_1, end_date=date(2026, 6, 30))
        await _add_report(session, WITH_REPORTS, ann_date=DUE_ON, end_date=date(2026, 9, 30))
        # 同一天公告的第二份报表（不同报告期）→ 仍然只算 1 次披露
        await _add_report(
            session,
            WITH_REPORTS,
            ann_date=DUE_ON,
            end_date=date(2026, 9, 30),
            report_type="Q3",
        )
        entry = _entry()
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["earnings_in_window"] is True
    assert result["earnings_note"] == (
        "窗口内含 2 次财报披露（10-19、10-21），结果可能被财报事件主导"
    )


@requires_pg
async def test_earnings_window_is_half_open_on_the_left_and_closed_on_the_right() -> None:
    """窗口 = ``(快照日, due_on]``：快照日当天的公告**不算**，到期日当天的**算**。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITH_REPORTS))
        # ① 只在快照日公告 → 窗口左开 → false
        await _add_report(session, WITH_REPORTS, ann_date=AS_OF, end_date=date(2026, 9, 30))
        left = _entry()
        session.add(left)
        await session.flush()
        left_result = await svc.evaluate_entry(session, left, as_of=DUE_ON)
        # ② 只在到期日公告 → 窗口右闭 → true
        await _add_report(
            session,
            WITH_REPORTS,
            ann_date=DUE_ON,
            end_date=date(2026, 9, 30),
            report_type="Q3",
        )
        right = _entry()
        session.add(right)
        await session.flush()
        right_result = await svc.evaluate_entry(session, right, as_of=DUE_ON)
        return left_result, right_result

    left, right = await _with_rollback(_go)
    assert left["earnings_in_window"] is False, "快照日当天的公告不属于 (快照日, due_on]"
    assert left["earnings_note"] == ""
    assert "earnings" not in left["missing_reason"], "false 是事实，不是缺数"
    assert right["earnings_in_window"] is True, "到期日当天的公告在窗口内（右闭）"
    assert right["earnings_note"] == ("窗口内含 1 次财报披露（10-21），结果可能被财报事件主导")


@requires_pg
async def test_earnings_window_false_when_only_post_due_announcements_exist() -> None:
    """PIT：只有 due_on **之后**才公告的财报 → ``false``（不拿事后公告当窗口内事件）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITH_REPORTS))
        await _add_report(
            session, WITH_REPORTS, ann_date=date(2026, 10, 23), end_date=date(2026, 9, 30)
        )
        entry = _entry()
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["earnings_in_window"] is False
    assert result["earnings_note"] == ""
    assert "earnings" not in result["missing_reason"]


@requires_pg
async def test_earnings_in_window_null_when_stock_has_no_financial_reports() -> None:
    """一只没有任何财报行的股票 → ``null`` + 原因（缺数据 ≠ "没有披露"）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITHOUT_REPORTS))
        entry = _entry(WITHOUT_REPORTS)
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["earnings_in_window"] is None, "无财报数据 = 不知道，不是 false"
    assert result["earnings_note"] == "", "无事件就没有提示（也不拿原因冒充提示）"
    assert result["missing_reason"]["earnings"] == "no_financial_reports"


@requires_pg
async def test_earnings_in_window_null_for_market_level_judgement() -> None:
    """市场级判断（无 symbol）：财报窗口不适用 → ``null`` + 明确原因。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        entry = _entry(None, snapshot=_snapshot(None, price=None))
        session.add(entry)
        await session.flush()
        return await svc.evaluate_entry(session, entry, as_of=DUE_ON)

    result = await _with_rollback(_go)
    assert result["earnings_in_window"] is None
    assert result["earnings_note"] == ""
    assert result["missing_reason"]["earnings"] == "market_level_no_symbol"


@requires_pg
async def test_earnings_window_does_not_add_a_query() -> None:
    """财报窗口并进标的 face 的同一条 SQL：评估的查询数仍 ≤3，且不随条目数增长。"""

    async def _go(session: AsyncSession, conn: Any) -> tuple[int, int, list[str]]:
        await _seed_quotes(session)
        await _clear_reports(session, await _stock_id(session, WITH_REPORTS))
        await _add_report(session, WITH_REPORTS, ann_date=DAY_1, end_date=date(2026, 9, 30))
        one = _entry()
        session.add(one)
        await session.flush()
        _, single, _ = await _count_queries(
            conn, lambda: svc.evaluate_entries(session, [one], as_of=DUE_ON)
        )
        two = _entry()
        session.add(two)
        await session.flush()
        _, pair, statements = await _count_queries(
            conn, lambda: svc.evaluate_entries(session, [one, two], as_of=DUE_ON)
        )
        return single, pair, statements

    single, pair, statements = await _with_rollback_conn(_go)
    assert single == pair, f"评估的查询数随条目数增长（N+1）：1 条 {single} → 2 条 {pair}"
    assert pair <= 3, f"评估用了 {pair} 条查询（契约要求 ≤3 条封顶）"
    # 财报数据必须出现在那批 SQL 里（并进去，而不是另起一条）
    assert any("financial_report_versions" in statement for statement in statements), (
        "财报窗口没有并进批查询"
    )


# ---------------------------------------------------------------------------
# 契约 §2 归因自洽：五类判定逐类 + 边界（净超额恰为 0）+ n<5
# ---------------------------------------------------------------------------


#: 十种「verdict × 净超额符号」的判定表（``excess`` 是**毛**超额，净 = 毛 − 0.12）：
#: ``(verdict, excess_pct, 期望自洽)``。``0.12`` 是成本线本身（净超额恰为 0）。
CONSISTENCY_CASES: tuple[tuple[str, float, bool], ...] = (
    # 「我错了」必须对应亏钱
    ("thesis_wrong", -3.0, True),
    ("thesis_wrong", 10.0, False),
    ("timing_wrong", -3.0, True),
    ("timing_wrong", 10.0, False),
    # 纪律问题与盈亏**独立** → 恒自洽
    ("discipline_break", 10.0, True),
    ("discipline_break", -10.0, True),
    ("discipline_break", 0.12, True),
    # 「我对了」必须对应赚钱（且赚过成本线）
    ("right_for_wrong_reason", 10.0, True),
    ("right_for_wrong_reason", -10.0, False),
    ("luck", 10.0, True),
    ("luck", -10.0, False),
    # 边界：净超额恰为 0（毛 = 12bp）→ 不算赚，四个"需要符号"的类别全不自洽
    ("thesis_wrong", 0.12, False),
    ("right_for_wrong_reason", 0.12, False),
)


async def _stats_with(
    session: AsyncSession, cases: tuple[tuple[str, float], ...]
) -> tuple[dict[str, Any], uuid.UUID]:
    user = uuid.uuid4()
    for verdict, excess in cases:
        entry = _entry(user_id=user)
        session.add(entry)
        await session.flush()
        session.add(
            DecisionJournalReview(
                entry_id=entry.id,
                outcome_pct=excess + 1.0,
                bench_pct=1.0,
                excess_pct=excess,
                invalidation_hit=False,
                verdict=verdict,
            )
        )
    await session.flush()
    return await svc.stats(session, user), user


@pytest.mark.parametrize(("verdict", "excess", "expected"), CONSISTENCY_CASES)
@requires_pg
async def test_consistency_per_verdict_judgement(
    verdict: str, excess: float, expected: bool
) -> None:
    """逐类判定：``thesis_wrong`` + 正超额 = 不自洽；``discipline_break`` **恒自洽**。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        stats, _ = await _stats_with(session, ((verdict, excess),))
        return stats

    result = await _with_rollback(_go)
    consistency = result["consistency"]
    assert consistency["n"] == 1
    assert consistency["consistent"] == (1 if expected else 0), (
        f"{verdict} 在 excess_pct={excess} 时的自洽判定错了"
    )
    # n < 5：比例与区间一律 null，只给 n/consistent（契约 §2）
    assert consistency["share"] is None
    assert consistency["ci_low"] is None
    assert consistency["ci_high"] is None


@requires_pg
async def test_consistency_share_and_wilson_interval_at_ten_reviews() -> None:
    """n ≥ 5：``share`` 与 Wilson 区间逐值一致（**复用** ``wilson_interval``）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        stats, _ = await _stats_with(session, tuple((v, e) for v, e, _ in CONSISTENCY_CASES))
        return stats

    result = await _with_rollback(_go)
    consistency = result["consistency"]
    total = len(CONSISTENCY_CASES)
    expected_consistent = sum(1 for _, _, ok in CONSISTENCY_CASES if ok)
    assert consistency["n"] == total == 13
    assert consistency["consistent"] == expected_consistent == 7
    assert consistency["share"] == round(expected_consistent / total, 4)
    interval = svc.wilson_interval(expected_consistent, total)
    assert interval is not None
    assert consistency["ci_low"] == interval["ci_low"]
    assert consistency["ci_high"] == interval["ci_high"]
    # 区间必须包住点估计（不是抄反了的两个数）
    assert consistency["ci_low"] <= consistency["share"] <= consistency["ci_high"]
    # 契约 §2 的免责文案**逐字**下发（前端原样展示）
    assert consistency["note"] == (
        '不自洽不代表你说谎：按过程评价自己（如"赚了但理由错"）本来就会与收益符号不一致'
    )


@requires_pg
async def test_consistency_is_null_below_five_reviews() -> None:
    """4 条已复盘（3 条自洽）→ 只给 ``n``/``consistent``，比例与区间都是 null。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        stats, _ = await _stats_with(
            session,
            (
                ("luck", 10.0),
                ("luck", 10.0),
                ("discipline_break", -10.0),
                ("thesis_wrong", 10.0),  # 不自洽
            ),
        )
        return stats

    result = await _with_rollback(_go)
    consistency = result["consistency"]
    assert result["reviewed_n"] == 4
    assert consistency["n"] == 4
    assert consistency["consistent"] == 3
    assert consistency["share"] is None, "n<5 不给点估计（不是 0.75）"
    assert consistency["ci_low"] is None
    assert consistency["ci_high"] is None
    assert consistency["note"], "免责文案与样本量无关，始终下发"


@requires_pg
async def test_consistency_is_empty_not_fabricated_for_a_new_user() -> None:
    """没有任何复盘 → ``n=0``、比例 null（0/0 不能变成 0% 或 100%）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        entry = _entry()
        session.add(entry)
        await session.flush()
        return await svc.stats(session, entry.user_id)

    result = await _with_rollback(_go)
    consistency = result["consistency"]
    assert consistency["n"] == 0
    assert consistency["consistent"] == 0
    assert consistency["share"] is None
    assert consistency["ci_low"] is None
    assert consistency["ci_high"] is None


@requires_pg
async def test_consistency_only_counts_reviewed_entries() -> None:
    """未复盘的条目只进 ``n``/``by_kind``，**不进**自洽率的分母。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        stats, user = await _stats_with(session, (("luck", 10.0), ("thesis_wrong", -3.0)))
        for _ in range(3):
            session.add(_entry(user_id=user))  # 未复盘
        await session.flush()
        return await svc.stats(session, user)

    result = await _with_rollback(_go)
    assert result["n"] == 5
    assert result["reviewed_n"] == 2
    consistency = result["consistency"]
    assert consistency["n"] == 2, "分母 = 已复盘且净超额可算，不含未复盘"
    assert consistency["consistent"] == 2
    assert consistency["share"] is None
