"""Quote repository: kline and latest quote queries."""

from datetime import date

from sqlalchemy import desc, exists, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.quote import DailyQuote


async def get_kline(
    db: AsyncSession,
    stock_id: int,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[DailyQuote]:
    stmt = (
        select(DailyQuote)
        .where(DailyQuote.stock_id == stock_id)
        .order_by(DailyQuote.trade_date)
    )
    if start_date:
        stmt = stmt.where(DailyQuote.trade_date >= start_date)
    if end_date:
        stmt = stmt.where(DailyQuote.trade_date <= end_date)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_latest_quote(db: AsyncSession, stock_id: int) -> DailyQuote | None:
    stmt = (
        select(DailyQuote)
        .where(DailyQuote.stock_id == stock_id)
        .order_by(desc(DailyQuote.trade_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_trade_date_bounds_for_stocks(
    db: AsyncSession,
    stock_ids: list[int],
    start_date: date,
    end_date: date,
) -> dict[int, tuple[date, date, int]]:
    """Return min/max trade_date and row count for each stock_id in date range."""
    if not stock_ids:
        return {}

    stmt = (
        select(
            DailyQuote.stock_id,
            func.min(DailyQuote.trade_date).label("min_date"),
            func.max(DailyQuote.trade_date).label("max_date"),
            func.count().label("row_count"),
        )
        .where(
            DailyQuote.stock_id.in_(stock_ids),
            DailyQuote.trade_date >= start_date,
            DailyQuote.trade_date <= end_date,
        )
        .group_by(DailyQuote.stock_id)
    )
    result = await db.execute(stmt)
    rows = result.all()

    data: dict[int, tuple[date, date, int]] = {}
    for row in rows:
        if row.min_date is None or row.max_date is None:
            continue
        data[row.stock_id] = (row.min_date, row.max_date, int(row.row_count))
    return data


async def trade_date_exists(db: AsyncSession, trade_date: date) -> bool:
    """Return True if any daily_quotes row exists for the given trade_date."""
    stmt = select(exists().where(DailyQuote.trade_date == trade_date))
    result = await db.execute(stmt)
    return result.scalar() is True


async def upsert_quotes(db: AsyncSession, quotes: list[DailyQuote]) -> int:
    """Bulk upsert daily quotes; returns the number of rows affected."""
    from sqlalchemy.dialects.postgresql import insert

    if not quotes:
        return 0

    values = [
        {
            "stock_id": q.stock_id,
            "trade_date": q.trade_date,
            "open": q.open,
            "high": q.high,
            "low": q.low,
            "close": q.close,
            "volume": q.volume,
            "amount": q.amount,
            "adj_factor": q.adj_factor,
            "source": q.source,
        }
        for q in quotes
    ]

    stmt = (
        insert(DailyQuote)
        .values(values)
        .on_conflict_do_update(
            constraint="uq_daily_quotes_stock_date",
            set_={
                "open": insert(DailyQuote).excluded.open,
                "high": insert(DailyQuote).excluded.high,
                "low": insert(DailyQuote).excluded.low,
                "close": insert(DailyQuote).excluded.close,
                "volume": insert(DailyQuote).excluded.volume,
                "amount": insert(DailyQuote).excluded.amount,
                # COALESCE：新行因子为 NULL（每日 ingest 不带因子）时不覆盖既有已回补值
                "adj_factor": func.coalesce(
                    insert(DailyQuote).excluded.adj_factor,
                    DailyQuote.__table__.c.adj_factor,
                ),
            },
        )
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount


async def latest_adj_factor_present(db: AsyncSession, stock_id: int) -> bool:
    """True if the stock's latest trade_date row has a non-null adj_factor.

    无行情行时返回 False（视为未回补，可触发首次拉取）。
    与 get_kline 的可用性口径（区间内全部行非空）相比偏宽松：
    每日 ingest 追加 NULL 因子新行后允许增量回补，避免"任一行非空即 skip"的死锁。
    """
    stmt = (
        select(DailyQuote.adj_factor)
        .where(DailyQuote.stock_id == stock_id)
        .order_by(desc(DailyQuote.trade_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none() is not None


async def update_adj_factors(
    db: AsyncSession, stock_id: int, factors: list[tuple[date, float]]
) -> int:
    """Bulk-update adj_factor on existing daily_quotes rows; returns rows updated."""
    if not factors:
        return 0
    values = ", ".join(
        # ::date 显式转型：VALUES 派生表中未定型字面量会被推断为 text，
        # 与 daily_quotes.trade_date(date) 比较时抛 date = text 无操作符错误
        f"({stock_id}, '{d.isoformat()}'::date, {f})" for d, f in factors
    )
    stmt = text(
        f"UPDATE daily_quotes AS dq SET adj_factor = v.adj_factor "
        f"FROM (VALUES {values}) AS v(stock_id, trade_date, adj_factor) "
        f"WHERE dq.stock_id = v.stock_id AND dq.trade_date = v.trade_date"
    )
    result = await db.execute(stmt)
    await db.flush()
    return result.rowcount
