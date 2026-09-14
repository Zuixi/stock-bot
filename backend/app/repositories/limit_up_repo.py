"""连板/情绪数据访问：权威限价、候选窗口、当日广度、情绪聚合。

口径与实测证据见 docs/design/limit-up-sentiment.md。
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.market_data import StockPriceLimit
from app.models.quote import DailyQuote


async def latest_quote_date(db: AsyncSession) -> date | None:
    return (await db.execute(select(func.max(DailyQuote.trade_date)))).scalar_one_or_none()


async def list_recent_trade_dates(db: AsyncSession, as_of: date, limit: int) -> list[date]:
    """窗口内交易日（升序）。

    真源是 daily_quotes 实际存在的日期，而不是 trade_cal 推算或 weekday()——库内没有
    持久化交易日历，而"哪些日子真有行情"本身就是最准的可用性判据（节假日后的永久
    缺口也是靠这个口径暴露的）。
    """
    stmt = (
        select(DailyQuote.trade_date)
        .where(DailyQuote.trade_date <= as_of)
        .group_by(DailyQuote.trade_date)
        .order_by(DailyQuote.trade_date.desc())
        .limit(limit)
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return sorted(rows)


async def missing_price_limit_dates(db: AsyncSession, dates: list[date]) -> list[date]:
    """给定交易日中，stock_price_limits 尚无任何行的那些（补漏判据）。"""
    if not dates:
        return []
    present = set(
        (await db.execute(
            select(StockPriceLimit.trade_date)
            .where(StockPriceLimit.trade_date.in_(dates))
            .group_by(StockPriceLimit.trade_date)
        )).scalars().all()
    )
    return [d for d in dates if d not in present]


async def has_price_limits(db: AsyncSession, as_of: date) -> bool:
    stmt = select(func.count()).select_from(StockPriceLimit).where(
        StockPriceLimit.trade_date == as_of
    ).limit(1)
    return bool((await db.execute(stmt)).scalar_one())


# asyncpg 单条语句 bind 参数上限 32767；本表 INSERT 每行 6 个绑定列（id 自增不参与），
# 单日约 5,499 行会一次绑定 6*5499=32994 个参数越界（实测 InterfaceError），故分批执行。
_UPSERT_CHUNK = 4000


async def upsert_price_limits(db: AsyncSession, rows: list[dict[str, Any]]) -> int:
    """幂等写入限价；同批内先按唯一键去重（pg 的 ON CONFLICT 不处理同批自冲突）。

    去重键必须是完整的唯一键 (trade_date, stock_id)——只按 stock_id 去重会静默
    丢掉其他日期的行（调用方目前按日分批，错只错在"以后有人合并多日"）。
    单日约 5,499 行超过 asyncpg 单语句 32767 绑定参数上限，按 _UPSERT_CHUNK 分批。
    """
    if not rows:
        return 0
    deduped = list({(r["trade_date"], r["stock_id"]): r for r in rows}.values())
    total = 0
    for i in range(0, len(deduped), _UPSERT_CHUNK):
        batch = deduped[i : i + _UPSERT_CHUNK]
        stmt = pg_insert(StockPriceLimit).values(batch).on_conflict_do_update(
            constraint="uq_price_limit_date_stock",
            set_={
                "pre_close": pg_insert(StockPriceLimit).excluded.pre_close,
                "up_limit": pg_insert(StockPriceLimit).excluded.up_limit,
                "down_limit": pg_insert(StockPriceLimit).excluded.down_limit,
            },
        )
        result = cast("CursorResult[Any]", await db.execute(stmt))
        total += int(result.rowcount)
    await db.flush()
    return total
