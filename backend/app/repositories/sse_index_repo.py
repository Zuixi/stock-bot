"""SSE index snapshot repository: bulk upsert, latest, intraday, and daily summary."""

from datetime import date

from sqlalchemy import desc, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.sse_index_snapshot import SseIndexSnapshot


async def bulk_upsert_snapshots(db: AsyncSession, rows: list[dict]) -> int:
    """Bulk upsert snapshot rows; returns the number of rows affected."""
    if not rows:
        return 0

    stmt = (
        insert(SseIndexSnapshot)
        .values(rows)
        .on_conflict_do_update(
            constraint="uq_sse_snapshots_code_time",
            set_={
                "name": insert(SseIndexSnapshot).excluded.name,
                "prev_close": insert(SseIndexSnapshot).excluded.prev_close,
                "open": insert(SseIndexSnapshot).excluded.open,
                "high": insert(SseIndexSnapshot).excluded.high,
                "low": insert(SseIndexSnapshot).excluded.low,
                "last": insert(SseIndexSnapshot).excluded.last,
                "chg_rate": insert(SseIndexSnapshot).excluded.chg_rate,
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount


async def get_latest_snapshots(
    db: AsyncSession,
    codes: list[str] | None = None,
) -> list[SseIndexSnapshot]:
    """Return the most recent snapshot for each index code."""
    subq = select(
        SseIndexSnapshot.code,
        func.max(SseIndexSnapshot.collect_time).label("max_time"),
    )
    if codes:
        subq = subq.where(SseIndexSnapshot.code.in_(codes))
    subq = subq.group_by(SseIndexSnapshot.code).subquery()

    stmt = select(SseIndexSnapshot).join(
        subq,
        (SseIndexSnapshot.code == subq.c.code)
        & (SseIndexSnapshot.collect_time == subq.c.max_time),
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_intraday_by_date(
    db: AsyncSession,
    code: str,
    trade_date: date,
) -> list[SseIndexSnapshot]:
    """Return all intraday snapshots for a given index on a specific date."""
    stmt = (
        select(SseIndexSnapshot)
        .where(SseIndexSnapshot.code == code, SseIndexSnapshot.trade_date == trade_date)
        .order_by(SseIndexSnapshot.collect_time)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_daily_summary(
    db: AsyncSession,
    codes: list[str] | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[SseIndexSnapshot]:
    """Return the last snapshot per (code, trade_date) within a date range."""
    subq = select(
        SseIndexSnapshot.code,
        SseIndexSnapshot.trade_date,
        func.max(SseIndexSnapshot.collect_time).label("max_time"),
    )
    if codes:
        subq = subq.where(SseIndexSnapshot.code.in_(codes))
    if start_date:
        subq = subq.where(SseIndexSnapshot.trade_date >= start_date)
    if end_date:
        subq = subq.where(SseIndexSnapshot.trade_date <= end_date)
    subq = subq.group_by(SseIndexSnapshot.code, SseIndexSnapshot.trade_date).subquery()

    stmt = (
        select(SseIndexSnapshot)
        .join(
            subq,
            (SseIndexSnapshot.code == subq.c.code)
            & (SseIndexSnapshot.collect_time == subq.c.max_time),
        )
        .order_by(SseIndexSnapshot.trade_date.desc())
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())
