"""PIT 截断等价契约（Q1）：**当日派生结果不得被未来数据改变**。

判据（可机械验证）：对同一查询日 D，在"全库含未来行"与"删掉 D 之后所有行"
两种状态下，派生读面的输出必须**逐字段相等**。任何不等都意味着该读面偷看了未来。

覆盖两类最容易长前视的读面：

1. 日级聚合（A 股全景广度）：按 ``trade_date`` 分组的聚合，若误用
   "最新一天"或未按窗口过滤，未来行会泄进当日结果；
2. 一致性检查（派生表 vs 底座重算）：底座重算若不带日期条件，未来行情
   会把当日家数改掉。

做法：全部在**一个回滚事务**里跑（Ruling 17）——先种 D 与 D+1..D+3 两批合成行，
算一遍；再 ``DELETE WHERE trade_date > D``，算第二遍；断言相等。库跑完与跑前
逐字节一致。
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, date, datetime, timedelta
from typing import Any

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.services import consistency_service
from app.services import market_panorama_ingest_service as panorama

_DEFAULT_ASYNC = "postgresql+asyncpg://stock_user:stock_pass@localhost:5433/stock_bot_test"
ASYNC_URL = os.environ.get("TEST_DATABASE_URL") or _DEFAULT_ASYNC

D = date(2026, 6, 10)
FUTURE = [D + timedelta(days=i) for i in (1, 2, 3)]
PREFIX = "TCE"  # truncation-contract-equivalence


def _pg_reachable() -> bool:
    async def _go() -> bool:
        try:
            conn = await asyncpg.connect(ASYNC_URL.replace("+asyncpg", ""))
        except Exception:
            return False
        await conn.close()
        return True

    return asyncio.run(_go())


requires_pg = pytest.mark.skipif(
    not _pg_reachable(), reason=f"PostgreSQL unavailable at {ASYNC_URL} (set TEST_DATABASE_URL)"
)


async def _with_rollback(fn: Any) -> Any:
    eng = create_async_engine(ASYNC_URL)
    try:
        async with eng.connect() as conn:
            trans = await conn.begin()
            session = AsyncSession(bind=conn, join_transaction_mode="create_savepoint")
            try:
                return await fn(session)
            finally:
                await session.close()
                await trans.rollback()
    finally:
        await eng.dispose()


async def _seed(db: AsyncSession) -> None:
    """D 日 3 只正常股 + 未来 3 日各 2 只极端股（涨幅/成交额足以改变任何聚合）."""
    rows: list[dict[str, Any]] = []
    for i in range(3):
        symbol = f"{PREFIX}{i:03d}"
        stock_id = (
            await db.execute(
                text(
                    """
                    INSERT INTO stocks (exchange, symbol, name, category, list_date, asof)
                    VALUES ('Shanghai_Stocks', :symbol, :name, '沪深A股', :list_date, :asof)
                    RETURNING id
                    """
                ),
                {
                    "symbol": symbol,
                    "name": f"截断测试{i}",
                    "list_date": date(2020, 1, 1),
                    "asof": datetime.now(UTC),
                },
            )
        ).scalar_one()
        rows.append(
            {
                "stock_id": stock_id,
                "trade_date": D,
                "close": 10.0 + i,
                "pct_chg": [2.0, -1.0, 0.5][i],
                "amount": 1_000.0 * (i + 1),
            }
        )

    # 未来行：涨停级涨幅 + 巨额成交，若泄进 D 日结果一定看得出来
    for j, day in enumerate(FUTURE):
        for k in range(2):
            symbol = f"{PREFIX}F{j}{k}"
            stock_id = (
                await db.execute(
                    text(
                        """
                        INSERT INTO stocks (exchange, symbol, name, category, list_date, asof)
                        VALUES ('Shanghai_Stocks', :symbol, :name, '沪深A股', :list_date, :asof)
                        RETURNING id
                        """
                    ),
                    {
                        "symbol": symbol,
                        "name": f"未来{j}{k}",
                        "list_date": date(2020, 1, 1),
                        "asof": datetime.now(UTC),
                    },
                )
            ).scalar_one()
            rows.append(
                {
                    "stock_id": stock_id,
                    "trade_date": day,
                    "close": 20.0,
                    "pct_chg": 9.9,
                    "amount": 9_000_000.0,
                }
            )

    await db.execute(
        text(
            """
            INSERT INTO daily_quotes (stock_id, trade_date, close, pct_chg, amount, source)
            VALUES (:stock_id, :trade_date, :close, :pct_chg, :amount, 'test')
            """
        ),
        rows,
    )
    await db.flush()


async def _breadth_for_day(db: AsyncSession) -> dict[str, Any]:
    rows = await panorama.compute_breadth_rows(db, D, D)
    assert len(rows) == 1, "窗口内只应有一天的广度"
    out = dict(rows[0])
    out.pop("trade_date", None)
    return out


async def _consistency_for_day(db: AsyncSession) -> list[dict[str, Any]]:
    checks = await consistency_service.check(db, day=D)
    return [{k: v for k, v in c.items() if k != "label"} for c in checks]


def _assert_same(before: Any, after: Any) -> None:
    assert before == after, f"未来数据改变了当日结果：\n before={before}\n after={after}"


@requires_pg
def test_breadth_is_truncation_equivalent() -> None:
    async def _scenario(db: AsyncSession) -> tuple[Any, Any]:
        await _seed(db)
        before = await _breadth_for_day(db)
        await db.execute(text("DELETE FROM daily_quotes WHERE trade_date > :d"), {"d": D})
        await db.flush()
        after = await _breadth_for_day(db)
        return before, after

    before, after = asyncio.run(_with_rollback(_scenario))
    _assert_same(before, after)
    # 且确实只统计了 D 日的 3 只（不是 9 只）
    assert before["up_count"] == 1
    assert before["down_count"] == 1
    assert before["flat_count"] == 1
    assert float(before["amount"]) == pytest.approx(6_000.0)


@requires_pg
def test_consistency_is_truncation_equivalent() -> None:
    async def _scenario(db: AsyncSession) -> tuple[Any, Any]:
        await _seed(db)
        before = await _consistency_for_day(db)
        await db.execute(text("DELETE FROM daily_quotes WHERE trade_date > :d"), {"d": D})
        await db.flush()
        after = await _consistency_for_day(db)
        return before, after

    before, after = asyncio.run(_with_rollback(_scenario))
    _assert_same(before, after)
    recomputed = {c["key"]: c["recomputed"] for c in before}
    assert recomputed["up_count"] == 1.0
    assert recomputed["down_count"] == 1.0
    assert recomputed["flat_count"] == 1.0


@requires_pg
def test_truncation_actually_changes_unfiltered_aggregate() -> None:
    """反证：去掉日期过滤后未来行会改变结果 —— 证明上面的相等不是"数据没生效"。

    不做这条，前两条可能因为"未来行根本没写进去"而假绿（门禁必须先红一次）。
    """

    async def _scenario(db: AsyncSession) -> tuple[float, float]:
        await _seed(db)
        filtered = (
            await db.execute(
                text("SELECT sum(amount) FROM daily_quotes WHERE trade_date = :d"), {"d": D}
            )
        ).scalar_one()
        unfiltered = (
            await db.execute(
                text(
                    "SELECT sum(amount) FROM daily_quotes q JOIN stocks s ON s.id = q.stock_id "
                    "WHERE s.symbol LIKE :p"
                ),
                {"p": f"{PREFIX}%"},
            )
        ).scalar_one()
        return float(filtered), float(unfiltered)

    filtered, unfiltered = asyncio.run(_with_rollback(_scenario))
    assert unfiltered > filtered * 1000, "未来行必须显著改变无过滤聚合，否则本文件失去意义"
