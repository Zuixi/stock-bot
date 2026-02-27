"""Stock repository: CRUD + filtering."""

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock, StockHistory
from app.schemas.stock import StockListParams


async def list_stocks(
    db: AsyncSession,
    params: StockListParams,
    offset: int,
    limit: int,
) -> tuple[list[Stock], int]:
    stmt = select(Stock)
    count_stmt = select(func.count()).select_from(Stock)

    if params.exchange:
        stmt = stmt.where(Stock.exchange == params.exchange)
        count_stmt = count_stmt.where(Stock.exchange == params.exchange)
    if params.category:
        stmt = stmt.where(Stock.category == params.category)
        count_stmt = count_stmt.where(Stock.category == params.category)
    if params.keyword:
        kw = f"%{params.keyword}%"
        condition = or_(Stock.symbol.ilike(kw), Stock.name.ilike(kw))
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (await db.execute(stmt.offset(offset).limit(limit))).scalars().all()
    return list(rows), total


async def get_stock_by_symbol(db: AsyncSession, symbol: str) -> Stock | None:
    result = await db.execute(select(Stock).where(Stock.symbol == symbol))
    return result.scalar_one_or_none()


async def get_stock_by_id(db: AsyncSession, stock_id: int) -> Stock | None:
    return await db.get(Stock, stock_id)


async def list_exchanges(db: AsyncSession) -> list[str]:
    result = await db.execute(select(Stock.exchange).distinct())
    return list(result.scalars().all())


async def list_categories(
    db: AsyncSession, exchange: str | None = None
) -> list[tuple[str, str, int]]:
    stmt = (
        select(Stock.exchange, Stock.category, func.count().label("count"))
        .group_by(Stock.exchange, Stock.category)
        .order_by(Stock.exchange, Stock.category)
    )
    if exchange:
        stmt = stmt.where(Stock.exchange == exchange)
    result = await db.execute(stmt)
    return [(row.exchange, row.category, row.count) for row in result]


async def upsert_stock(db: AsyncSession, stock: Stock) -> Stock:
    """Insert or update a stock record based on (exchange, symbol)."""
    from sqlalchemy.dialects.postgresql import insert

    values = {
        "exchange": stock.exchange,
        "symbol": stock.symbol,
        "name": stock.name,
        "full_name": stock.full_name,
        "category": stock.category,
        "list_date": stock.list_date,
        "csrc_code": stock.csrc_code,
        "csrc_desc": stock.csrc_desc,
        "province": stock.province,
        "status": stock.status,
        "asof": stock.asof,
    }
    stmt = (
        insert(Stock)
        .values(**values)
        .on_conflict_do_update(
            constraint="uq_stocks_exchange_symbol",
            set_={k: v for k, v in values.items() if k not in ("exchange", "symbol")},
        )
        .returning(Stock)
    )
    result = await db.execute(stmt)
    await db.flush()
    row = result.fetchone()
    return row[0]


async def insert_stock_history(db: AsyncSession, record: StockHistory) -> None:
    db.add(record)
    await db.flush()
