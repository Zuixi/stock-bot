"""Repository for DailyBasicIndicator — daily fundamental indicators."""

from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_basic import DailyBasicIndicator


async def get_daily_basic(
    db: AsyncSession,
    stock_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[DailyBasicIndicator]:
    """Return daily_basic rows for a stock, ordered by trade_date asc."""
    stmt = (
        select(DailyBasicIndicator)
        .where(DailyBasicIndicator.stock_id == stock_id)
        .order_by(DailyBasicIndicator.trade_date)
    )
    if start_date:
        stmt = stmt.where(DailyBasicIndicator.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(DailyBasicIndicator.trade_date <= end_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_daily_basic(
    db: AsyncSession, stock_id: int
) -> DailyBasicIndicator | None:
    """Return the latest daily_basic row for a stock."""
    stmt = (
        select(DailyBasicIndicator)
        .where(DailyBasicIndicator.stock_id == stock_id)
        .order_by(desc(DailyBasicIndicator.trade_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_daily_basics(
    db: AsyncSession, records: list[DailyBasicIndicator]
) -> int:
    """Bulk upsert daily_basic rows; returns rows affected."""
    if not records:
        return 0

    values = [
        {
            "stock_id": r.stock_id,
            "trade_date": r.trade_date,
            "close": r.close,
            "turnover_rate": r.turnover_rate,
            "turnover_rate_f": r.turnover_rate_f,
            "volume_ratio": r.volume_ratio,
            "pe": r.pe,
            "pe_ttm": r.pe_ttm,
            "pb": r.pb,
            "ps": r.ps,
            "ps_ttm": r.ps_ttm,
            "dv_ratio": r.dv_ratio,
            "dv_ttm": r.dv_ttm,
            "total_share": r.total_share,
            "float_share": r.float_share,
            "free_share": r.free_share,
            "total_mv": r.total_mv,
            "circ_mv": r.circ_mv,
            "source": r.source,
        }
        for r in records
    ]

    stmt = (
        insert(DailyBasicIndicator)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_daily_basic_stock_date",
            set_={
                "close": insert(DailyBasicIndicator).excluded.close,
                "turnover_rate": insert(DailyBasicIndicator).excluded.turnover_rate,
                "turnover_rate_f": insert(DailyBasicIndicator).excluded.turnover_rate_f,
                "volume_ratio": insert(DailyBasicIndicator).excluded.volume_ratio,
                "pe": insert(DailyBasicIndicator).excluded.pe,
                "pe_ttm": insert(DailyBasicIndicator).excluded.pe_ttm,
                "pb": insert(DailyBasicIndicator).excluded.pb,
                "ps": insert(DailyBasicIndicator).excluded.ps,
                "ps_ttm": insert(DailyBasicIndicator).excluded.ps_ttm,
                "dv_ratio": insert(DailyBasicIndicator).excluded.dv_ratio,
                "dv_ttm": insert(DailyBasicIndicator).excluded.dv_ttm,
                "total_share": insert(DailyBasicIndicator).excluded.total_share,
                "float_share": insert(DailyBasicIndicator).excluded.float_share,
                "free_share": insert(DailyBasicIndicator).excluded.free_share,
                "total_mv": insert(DailyBasicIndicator).excluded.total_mv,
                "circ_mv": insert(DailyBasicIndicator).excluded.circ_mv,
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
