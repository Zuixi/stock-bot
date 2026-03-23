"""Quote service: K-line and latest quote with caching."""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import quote_repo, stock_repo
from app.schemas.quote import DailyQuoteOut, KlineResponse, LatestQuoteOut

logger = logging.getLogger(__name__)


async def get_kline(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
    start_date: date | None = None,
    end_date: date | None = None,
) -> KlineResponse | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    start_str = start_date.isoformat() if start_date else "all"
    end_str = end_date.isoformat() if end_date else "all"
    cache_key = f"quote:kline:{exchange}:{symbol}:{start_str}:{end_str}"
    cached = await cache.get(cache_key)
    if cached:
        return KlineResponse(**cached)

    quotes = await quote_repo.get_kline(db, stock.id, start_date, end_date)
    data = [DailyQuoteOut.model_validate(q) for q in quotes]
    response = KlineResponse(symbol=symbol, name=stock.name, exchange=exchange, data=data)
    await cache.set(cache_key, response.model_dump(mode="json"), ttl=600)
    return response


async def get_latest_quote(
    db: AsyncSession, cache: CacheClient, exchange: str, symbol: str
) -> LatestQuoteOut | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    cache_key = f"quote:latest:{exchange}:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return LatestQuoteOut(**cached)

    quote = await quote_repo.get_latest_quote(db, stock.id)
    if quote is None:
        return None

    out = LatestQuoteOut(
        symbol=symbol,
        name=stock.name,
        exchange=exchange,
        trade_date=quote.trade_date,
        close=float(quote.close),
        volume=quote.volume,
        amount=float(quote.amount) if quote.amount else None,
    )
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=600)
    return out
