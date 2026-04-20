"""Index daily repository: kline, latest snapshot, and bulk upsert."""

from datetime import date

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.index_daily import IndexDaily


async def get_kline(
    db: AsyncSession,
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[IndexDaily]:
    stmt = (
        select(IndexDaily)
        .where(IndexDaily.ts_code == ts_code)
        .order_by(IndexDaily.trade_date)
    )
    if start_date:
        stmt = stmt.where(IndexDaily.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(IndexDaily.trade_date <= end_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest(
    db: AsyncSession,
    ts_codes: list[str],
) -> list[IndexDaily]:
    """Return the latest row for each ts_code in the given list."""
    if not ts_codes:
        return []

    from sqlalchemy import func

    subq = (
        select(
            IndexDaily.ts_code,
            func.max(IndexDaily.trade_date).label("max_date"),
        )
        .where(IndexDaily.ts_code.in_(ts_codes))
        .group_by(IndexDaily.ts_code)
        .subquery()
    )
    stmt = (
        select(IndexDaily)
        .join(
            subq,
            (IndexDaily.ts_code == subq.c.ts_code)
            & (IndexDaily.trade_date == subq.c.max_date),
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_single(
    db: AsyncSession,
    ts_code: str,
) -> IndexDaily | None:
    stmt = (
        select(IndexDaily)
        .where(IndexDaily.ts_code == ts_code)
        .order_by(desc(IndexDaily.trade_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def upsert_index_dailies(db: AsyncSession, rows: list[IndexDaily]) -> int:
    """Bulk upsert index daily rows; returns the number of rows affected."""
    from sqlalchemy.dialects.postgresql import insert

    if not rows:
        return 0

    values = [
        {
            "ts_code": r.ts_code,
            "trade_date": r.trade_date,
            "open": r.open,
            "high": r.high,
            "low": r.low,
            "close": r.close,
            "pre_close": r.pre_close,
            "volume": r.volume,
            "amount": r.amount,
        }
        for r in rows
    ]

    stmt = (
        insert(IndexDaily)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_index_dailies_code_date",
            set_={
                "open": insert(IndexDaily).excluded.open,
                "high": insert(IndexDaily).excluded.high,
                "low": insert(IndexDaily).excluded.low,
                "close": insert(IndexDaily).excluded.close,
                "pre_close": insert(IndexDaily).excluded.pre_close,
                "volume": insert(IndexDaily).excluded.volume,
                "amount": insert(IndexDaily).excluded.amount,
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
