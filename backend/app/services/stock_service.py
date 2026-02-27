"""Stock service: list, search, and exchange metadata."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import stock_repo
from app.schemas.common import PageParams
from app.schemas.stock import CategoryOut, ExchangeOut, StockListParams, StockOut

logger = logging.getLogger(__name__)

_EXCHANGES = [
    ExchangeOut(code="Shanghai_Stocks", name_cn="上海证券交易所"),
    ExchangeOut(code="Shenzen_Stocks", name_cn="深圳证券交易所"),
    ExchangeOut(code="Beijing_Stocks", name_cn="北京证券交易所"),
]


async def list_stocks(
    db: AsyncSession,
    cache: CacheClient,
    params: StockListParams,
    page_params: PageParams,
) -> tuple[list[StockOut], int]:
    cache_key = (
        f"stock:list:{params.exchange or 'all'}:{params.category or 'all'}"
        f":{params.keyword or ''}:{page_params.page}:{page_params.page_size}"
    )
    cached = await cache.get(cache_key)
    if cached:
        return [StockOut(**s) for s in cached["items"]], cached["total"]

    stocks, total = await stock_repo.list_stocks(
        db, params, offset=page_params.offset, limit=page_params.page_size
    )
    items = [StockOut.model_validate(s) for s in stocks]
    await cache.set(
        cache_key,
        {"items": [s.model_dump(mode="json") for s in items], "total": total},
        ttl=300,
    )
    return items, total


async def get_stock(
    db: AsyncSession, cache: CacheClient, symbol: str
) -> StockOut | None:
    cache_key = f"stock:detail:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return StockOut(**cached)

    stock = await stock_repo.get_stock_by_symbol(db, symbol)
    if stock is None:
        return None
    out = StockOut.model_validate(stock)
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=3600)
    return out


async def list_exchanges() -> list[ExchangeOut]:
    return _EXCHANGES


async def list_categories(
    db: AsyncSession, cache: CacheClient, exchange: str | None = None
) -> list[CategoryOut]:
    cache_key = f"stock:categories:{exchange or 'all'}"
    cached = await cache.get(cache_key)
    if cached:
        return [CategoryOut(**c) for c in cached]

    rows = await stock_repo.list_categories(db, exchange)
    result = [CategoryOut(exchange=r[0], category=r[1], count=r[2]) for r in rows]
    await cache.set(cache_key, [c.model_dump() for c in result], ttl=600)
    return result
