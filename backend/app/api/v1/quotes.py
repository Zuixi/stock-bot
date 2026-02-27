"""Quote endpoints: kline and latest quote."""

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.quote import KlineResponse, LatestQuoteOut
from app.services import quote_service

router = APIRouter()


@router.get("/{symbol}/daily", response_model=KlineResponse)
async def get_kline(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    start: date | None = Query(None, description="Start date (YYYY-MM-DD)"),
    end: date | None = Query(None, description="End date (YYYY-MM-DD)"),
) -> KlineResponse:
    result = await quote_service.get_kline(db, cache, symbol, start, end)
    if result is None:
        raise not_found_response("Stock", symbol)
    return result


@router.get("/{symbol}/latest", response_model=LatestQuoteOut)
async def get_latest_quote(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
) -> LatestQuoteOut:
    result = await quote_service.get_latest_quote(db, cache, symbol)
    if result is None:
        raise not_found_response("Quote", symbol)
    return result
