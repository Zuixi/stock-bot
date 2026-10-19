"""spec §S11.3：`evaluation.unlock_in_window` / `unlock_note`（与财报窗口同构的三态）。

这一组钉的是**契约本身**，不是实现细节：

1. **三态**：``true`` = 窗口内（``(快照日, due_on]``）有解禁且该解禁日合计占总股本 ≥3%；
   ``false`` = 有解禁记录但窗口内没有 ≥3% 的；``null`` = 该股没有任何（PIT 可见的）
   解禁记录 —— ``null`` 只写**降级键** ``missing_reason["unlock"]``，**不阻塞复盘**。
2. **PIT**：只用 ``ann_date <= due_on`` 的记录（公告后才可知）。这里比财报窗口更严：
   财报的 ``all_rows`` 不过滤公告日（所以"事后才公告"会得到 ``false``），解禁把
   ``all_rows`` 也按 PIT 过滤 —— 事后才公告的计划在观察日**根本不存在**，只有 ``null``
   才是诚实的；写成 ``false`` 等于用未来信息说"有记录但不在窗口内"。
3. **窗口边界**：(快照日, due_on] 左开右闭，判据是 ``float_date``（解禁日，事件发生的那天），
   不是 ``ann_date``。
4. **不新增查询**：解禁窗口与财报窗口并进标的 face 的同一条批 SQL（``≤3`` 条封顶），
   并断言那条 SQL 里确实出现了 ``share_floats``。

数据纪律（沿用 Ruling 17）：股票行用测试库里真实存在的 ``600519`` / ``600036``，
解禁行与行情在**回滚事务**里构造（测试库 ``share_floats`` / 这批日期都是空的），
跑完库与跑前一致；库不可达时 skip 而非红。
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date, datetime
from typing import Any

import asyncpg
import pytest
from sqlalchemy import event, insert, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.models.index_daily import IndexDaily
from app.models.journal import DecisionJournalEntry, DecisionJournalReview
from app.models.market_data import ShareFloat
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.services import journal_service as svc

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL") or _DEFAULT_ASYNC
SYNC_URL = ASYNC_URL.replace("+asyncpg", "")

AS_OF = date(2026, 10, 16)  # 快照日（窗口左端点，**不含**）
DAY_1 = date(2026, 10, 19)  # 窗口内的解禁日
DUE_ON = date(2026, 10, 21)  # 到期日（窗口右端点，**含**）
PRICE = 100.0
HS300_START, HS300_END = 4000.0, 4040.0

WITH_UNLOCK = "600519"
WITHOUT_UNLOCK = "600036"


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


async def _seed_quotes(session: AsyncSession) -> None:
    """两只标的的日线（入场 100 → 到期 110）+ 沪深300 起止（让结果"就绪"）。"""
    ids = {symbol: await _stock_id(session, symbol) for symbol in (WITH_UNLOCK, WITHOUT_UNLOCK)}
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


async def _add_unlock(
    session: AsyncSession,
    symbol: str,
    *,
    ann_date: date | None,
    float_date: date,
    float_share: float = 1000.0,
    float_ratio: float = 4.2,
    holder_name: str = "首发前股东",
    share_type: str = "首发原始股",
) -> None:
    """写一行解禁明细（``share_floats`` 是**持有人级**：同日多行必须被合计成一次解禁）。"""
    session.add(
        ShareFloat(
            ann_date=ann_date,
            float_date=float_date,
            ts_code=f"{symbol}.SH",
            float_share=float_share,
            float_ratio=float_ratio,
            holder_name=holder_name,
            share_type=share_type,
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


def _entry(symbol: str | None = WITH_UNLOCK, **overrides: Any) -> DecisionJournalEntry:
    payload: dict[str, Any] = {
        "user_id": uuid.uuid4(),
        "symbol": symbol,
        "kind": "watch",
        "bet_type": "direction" if symbol else "market",
        "position_size": "none",
        "thesis": "解禁前后博弈",
        "invalidation": {"kind": "date_only", "value": DUE_ON.isoformat()},
        "horizon_days": 7,
        "snapshot": _snapshot(symbol),
        "created_at": datetime(2026, 10, 16, 15, 0),
        "due_on": DUE_ON,
    }
    payload.update(overrides)
    return DecisionJournalEntry(**payload)


async def _evaluate(session: AsyncSession, entry: DecisionJournalEntry) -> dict[str, Any]:
    session.add(entry)
    await session.flush()
    return await svc.evaluate_entry(session, entry, as_of=DUE_ON)


# ---------------------------------------------------------------------------
# 三态 + 人话 + 窗口边界
# ---------------------------------------------------------------------------


@requires_pg
async def test_unlock_in_window_true_with_human_note() -> None:
    """窗口内有 1 次 4.2% 的解禁（10-19）→ ``true`` + spec §S11.3 的人话说明。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _add_unlock(session, WITH_UNLOCK, ann_date=date(2026, 10, 14), float_date=DAY_1)
        return await _evaluate(session, _entry())

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is True
    assert result["unlock_note"] == (
        "窗口内含 1 次解禁（10-19，占总股本 4.2%），结果可能被解禁冲击主导"
    )
    assert "unlock" not in result["missing_reason"], "有数据就不该有缺数原因"
    assert result["unlock_in_window"] is not False
    assert result["unlock_in_window"] is not None


@requires_pg
async def test_unlock_in_window_aggregates_holders_and_counts_distinct_days() -> None:
    """同日两个持有人（1.5% + 1.7%）= **一次** 3.2% 的解禁；两天都要进 note。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _add_unlock(
            session,
            WITH_UNLOCK,
            ann_date=AS_OF,
            float_date=DAY_1,
            float_share=100.0,
            float_ratio=1.5,
            holder_name="股东甲",
        )
        await _add_unlock(
            session,
            WITH_UNLOCK,
            ann_date=AS_OF,
            float_date=DAY_1,
            float_share=200.0,
            float_ratio=1.7,
            holder_name="股东乙",
        )
        await _add_unlock(
            session,
            WITH_UNLOCK,
            ann_date=AS_OF,
            float_date=DUE_ON,
            float_share=300.0,
            float_ratio=4.0,
            holder_name="股东丙",
        )
        return await _evaluate(session, _entry())

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is True
    assert result["unlock_note"] == (
        "窗口内含 2 次解禁（10-19，占总股本 3.2%；10-21，占总股本 4.0%），结果可能被解禁冲击主导"
    )


@requires_pg
async def test_unlock_window_uses_the_latest_announcement_of_the_same_tranche() -> None:
    """同一笔解禁被多次公告（初次 + 修订）→ **只算 PIT 可见的最新那一次**。

    唯一键含 ``ann_date``，所以库里会同时存着两次公告；直接把两行相加会把同一笔
    解禁算两遍（真库实测某只单日合计 119%，按最新公告只有 59.6%）。
    """

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _add_unlock(
            session,
            WITH_UNLOCK,
            ann_date=date(2026, 10, 1),
            float_date=DAY_1,
            float_share=2000.0,
            float_ratio=9.9,
            holder_name="首发前股东",
        )
        await _add_unlock(
            session,
            WITH_UNLOCK,
            ann_date=date(2026, 10, 14),
            float_date=DAY_1,
            float_share=1000.0,
            float_ratio=4.2,
            holder_name="首发前股东",
        )
        return await _evaluate(session, _entry())

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is True
    assert result["unlock_note"] == (
        "窗口内含 1 次解禁（10-19，占总股本 4.2%），结果可能被解禁冲击主导"
    )


@requires_pg
async def test_unlock_window_is_half_open_on_the_left_and_closed_on_the_right() -> None:
    """窗口 = ``(快照日, due_on]``（看 ``float_date``）：快照日当天不算，到期日当天算。"""

    async def _go(session: AsyncSession) -> tuple[dict[str, Any], dict[str, Any]]:
        await _seed_quotes(session)
        # 唯一键不含 float_date，同日同持有人同类型不能写两行 → 用不同 holder_name 区分
        await _add_unlock(
            session, WITH_UNLOCK, ann_date=AS_OF, float_date=AS_OF, holder_name="股东甲"
        )
        left = await _evaluate(session, _entry())
        await _add_unlock(
            session, WITH_UNLOCK, ann_date=AS_OF, float_date=DUE_ON, holder_name="股东乙"
        )
        right = await _evaluate(session, _entry())
        return left, right

    left, right = await _with_rollback(_go)
    assert left["unlock_in_window"] is False, "快照日当天的解禁不属于 (快照日, due_on]"
    assert left["unlock_note"] == ""
    assert "unlock" not in left["missing_reason"], "false 是事实，不是缺数"
    assert right["unlock_in_window"] is True, "到期日当天的解禁在窗口内（右闭）"
    assert right["unlock_note"] == (
        "窗口内含 1 次解禁（10-21，占总股本 4.2%），结果可能被解禁冲击主导"
    )


@requires_pg
async def test_unlock_window_false_when_only_small_unlocks_exist() -> None:
    """窗口内有解禁但只有 2.5%（< 3%）→ ``false``（规模不足以主导结果）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _add_unlock(session, WITH_UNLOCK, ann_date=AS_OF, float_date=DAY_1, float_ratio=2.5)
        return await _evaluate(session, _entry())

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is False
    assert result["unlock_note"] == ""
    assert "unlock" not in result["missing_reason"]


@requires_pg
async def test_unlock_in_window_null_when_stock_has_no_share_float_rows() -> None:
    """一只没有任何解禁行的股票 → ``null`` + 降级键（缺数据 ≠ "不会解禁"）。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        return await _evaluate(session, _entry(WITHOUT_UNLOCK))

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is None, "无解禁数据 = 不知道，不是 false"
    assert result["unlock_note"] == "", "无事件就没有提示（也不拿原因冒充提示）"
    assert result["missing_reason"]["unlock"] == "no_share_float_rows"


@requires_pg
async def test_unlock_in_window_null_for_market_level_judgement() -> None:
    """市场级判断（无 symbol）：解禁窗口不适用 → ``null`` + 明确原因。"""

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        entry = _entry(None, snapshot=_snapshot(None, price=None))
        return await _evaluate(session, entry)

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is None
    assert result["unlock_note"] == ""
    assert result["missing_reason"]["unlock"] == "market_level_no_symbol"


@requires_pg
async def test_unlock_window_ignores_plans_announced_after_the_due_date() -> None:
    """PIT：计划在 due_on **之后**才公告 → 观察日没有这条记录 → ``null``（不是 false）。

    比财报窗口更严：财报的 ``all_rows`` 不过滤公告日，解禁把 ``all_rows`` 也按 PIT 过滤。
    事后才公告的计划在观察日根本不可知，写成 ``false``（"有记录但不在窗口内"）等于
    用未来信息撒谎。
    """

    async def _go(session: AsyncSession) -> dict[str, Any]:
        await _seed_quotes(session)
        await _add_unlock(session, WITH_UNLOCK, ann_date=date(2026, 10, 23), float_date=DAY_1)
        return await _evaluate(session, _entry())

    result = await _with_rollback(_go)
    assert result["unlock_in_window"] is None
    assert result["missing_reason"]["unlock"] == "no_share_float_rows"


@requires_pg
async def test_unlock_null_does_not_block_review() -> None:
    """``missing_reason["unlock"]`` 是**降级键**：照样能复盘（spec §S11.3 / §S9 澄清）。

    否则"没有解禁记录"的股票（真实数据里占多数）会永久评不了分 —— 一次回归就能把复盘
    功能整体废掉，所以这条用**真实写入**证明，而不是只看字段。
    """

    async def _go(session: AsyncSession) -> tuple[DecisionJournalReview, DecisionJournalEntry]:
        await _seed_quotes(session)
        entry = _entry(WITHOUT_UNLOCK)
        user = uuid.UUID(str(entry.user_id))
        await _evaluate(session, entry)  # 结果就绪（有行情/基准）
        review = await svc.review_entry(
            session,
            user,
            entry.id,
            verdict="luck",
            note="解禁数据缺失不影响归因",
            now=datetime(2026, 10, 21, 16, 0),
        )
        return review, entry

    review, _entry_row = await _with_rollback(_go)
    assert review.verdict == "luck"
    assert review.outcome_pct is not None
    assert review.bench_pct is not None


@requires_pg
async def test_unlock_window_does_not_add_a_query() -> None:
    """解禁窗口并进标的 face 的同一条 SQL：查询数仍 ≤3，且不随条目数增长。"""

    async def _go(session: AsyncSession, conn: Any) -> tuple[int, int, list[str]]:
        await _seed_quotes(session)
        await _add_unlock(session, WITH_UNLOCK, ann_date=AS_OF, float_date=DAY_1)
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
    assert any("share_floats" in statement for statement in statements), (
        "解禁窗口没有并进批查询（另起了一条 SELECT share_floats）"
    )
