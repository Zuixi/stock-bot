"""Stock service: list, search, and exchange metadata."""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import stock_repo
from app.schemas.common import PageParams
from app.schemas.stock import (
    CategoryOut, ExchangeOut, StockEnrichedOut, StockListParams, StockOut,
)

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
    db: AsyncSession, cache: CacheClient, exchange: str, symbol: str
) -> StockOut | None:
    cache_key = f"stock:detail:{exchange}:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return StockOut(**cached)

    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None
    out = StockOut.model_validate(stock)
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=3600)
    return out


async def get_stock_enriched(
    db: AsyncSession, cache: CacheClient, exchange: str, symbol: str,
) -> StockEnrichedOut | None:
    """Single stock with latest quote + daily_basic enriched."""
    cache_key = f"stock:enriched:{exchange}:{symbol}"
    cached = await cache.get(cache_key)
    if cached:
        return StockEnrichedOut(**cached)

    from app.services.market_service import get_stocks_enriched_by_symbols  # noqa: PLC0415

    enriched_list = await get_stocks_enriched_by_symbols(db, [symbol])
    if not enriched_list:
        return None
    out = enriched_list[0]
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=300)
    return out


def list_exchanges() -> list[ExchangeOut]:
    return _EXCHANGES


async def list_stocks_enriched(
    db: AsyncSession,
    cache: CacheClient,
    params: StockListParams,
    page_params: PageParams,
) -> tuple[list[StockEnrichedOut], int]:
    """Paginated stock list with latest quote + daily_basic enriched.

    When sort_by is provided, fetches ALL matching stocks, enriches them,
    sorts in Python, then paginates. This adds ~70ms for 2300 stocks (LATERAL).
    Without sort_by, uses the original pagination-then-enrich flow.
    """
    from app.services.market_service import get_stocks_enriched_by_symbols  # noqa: PLC0415

    if params.sort_by:
        # ── Sort path: fetch all → enrich all → sort → paginate ──
        all_stocks, total = await stock_repo.list_stocks(
            db, params, offset=0, limit=10_000,
        )
        if not all_stocks:
            return [], 0
        symbols = [s.symbol for s in all_stocks]
        enriched_list = await get_stocks_enriched_by_symbols(db, symbols)
        enriched_map = {e.symbol: e for e in enriched_list}

        items: list[StockEnrichedOut] = []
        for s in all_stocks:
            enriched = enriched_map.get(s.symbol)
            if enriched:
                items.append(enriched)
            else:
                base = StockOut.model_validate(s)
                items.append(StockEnrichedOut(**base.model_dump()))

        # Sort
        reverse = params.sort_order == "desc"
        key = _sort_key_for(params.sort_by, reverse)
        items.sort(key=key, reverse=reverse)

        # Paginate
        return items[page_params.offset : page_params.offset + page_params.page_size], total

    # ── Fast path: paginate first, enrich only the page ──
    stocks, total = await stock_repo.list_stocks(
        db, params, offset=page_params.offset, limit=page_params.page_size,
    )
    if not stocks:
        return [], 0

    symbols = [s.symbol for s in stocks]
    enriched_list = await get_stocks_enriched_by_symbols(db, symbols)
    enriched_map = {e.symbol: e for e in enriched_list}

    items: list[StockEnrichedOut] = []
    for s in stocks:
        enriched = enriched_map.get(s.symbol)
        if enriched:
            items.append(enriched)
        else:
            base = StockOut.model_validate(s)
            items.append(StockEnrichedOut(**base.model_dump()))

    return items, total


def _sort_key_for(field: str, reverse: bool):
    """Return a sort key function for the given StockEnrichedOut field."""
    field_map = {
        "latestPrice": lambda s: (s.latest_price is None, s.latest_price or 0),
        "changePercent": lambda s: (s.change_percent is None, s.change_percent or 0),
        "turnover": lambda s: (s.amount is None, s.amount or 0),
        "marketCap": lambda s: (s.total_mv is None, s.total_mv or 0),
        "pe": lambda s: (s.pe_ttm is None, s.pe_ttm or 0),
        "symbol": lambda s: s.symbol or "",
        "name": lambda s: s.name or "",
    }
    return field_map.get(field, lambda s: s.symbol or "")


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
