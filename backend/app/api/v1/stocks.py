"""Stock endpoints: list, detail, exchanges, categories."""

from fastapi import APIRouter, Depends, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.common import PageParams, PagedResponse
from app.schemas.stock import CategoryOut, ExchangeOut, StockListParams, StockOut
from app.services import stock_service

router = APIRouter()


@router.get("", response_model=PagedResponse[StockOut])
async def list_stocks(
    db: DbDep,
    cache: CacheDep,
    exchange: str | None = Query(None),
    category: str | None = Query(None),
    keyword: str | None = Query(None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> PagedResponse[StockOut]:
    params = StockListParams(exchange=exchange, category=category, keyword=keyword)
    page_params = PageParams(page=page, page_size=page_size)
    items, total = await stock_service.list_stocks(db, cache, params, page_params)
    return PagedResponse.build(items=items, total=total, params=page_params)


@router.get("/exchanges", response_model=list[ExchangeOut])
async def list_exchanges() -> list[ExchangeOut]:
    return await stock_service.list_exchanges()


@router.get("/categories", response_model=list[CategoryOut])
async def list_categories(
    db: DbDep,
    cache: CacheDep,
    exchange: str | None = Query(None),
) -> list[CategoryOut]:
    return await stock_service.list_categories(db, cache, exchange)


@router.get("/{symbol}", response_model=StockOut)
async def get_stock(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
) -> StockOut:
    stock = await stock_service.get_stock(db, cache, symbol)
    if stock is None:
        raise not_found_response("Stock", symbol)
    return stock
