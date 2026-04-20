"""Market endpoints for dashboard data."""

from datetime import date, timedelta
from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.quote import IndexDailyOut, IndexKlineResponse
from app.schemas.stock import StockOut
from app.services import market_service

router = APIRouter()


@router.get("/indices", response_model=list[dict])
async def list_market_indices(cache: CacheDep) -> list[dict]:
    return await market_service.list_market_indices(cache=cache)


@router.get("/distribution", response_model=list[dict])
async def get_distribution(cache: CacheDep) -> list[dict]:
    return await market_service.get_distribution(cache=cache)


@router.get("/sectors", response_model=list[dict])
async def get_sectors(cache: CacheDep) -> list[dict]:
    return await market_service.get_sectors(cache=cache)


@router.get("/capital-flow", response_model=list[dict])
async def get_capital_flow(cache: CacheDep) -> list[dict]:
    return await market_service.get_capital_flow(cache=cache)


@router.get("/hot-boards", response_model=list[dict])
async def get_hot_boards(
    cache: CacheDep,
    category: Literal["industry", "concept", "region"] = Query(default="industry"),
) -> list[dict]:
    return await market_service.get_hot_boards(category, cache=cache)


@router.get("/indices/{ts_code}/kline", response_model=IndexKlineResponse)
async def get_index_kline(
    ts_code: str,
    cache: CacheDep,
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
) -> IndexKlineResponse:
    """Return index daily K-line data for charting."""
    if start is None:
        start = date.today() - timedelta(days=365)
    data = await market_service.get_index_kline(
        ts_code, start_date=start, end_date=end, cache=cache,
    )
    name = market_service.INDEX_NAME_MAP.get(ts_code, ts_code)
    return IndexKlineResponse(
        ts_code=ts_code,
        name=name,
        data=[IndexDailyOut.model_validate(d) for d in data],
    )


@router.get("/sw-industry/tree", response_model=list[dict])
async def get_sw_industry_tree(cache: CacheDep) -> list[dict]:
    return await market_service.get_sw_industry_tree(cache=cache)


@router.get("/sw-industry/{level1_code}/stocks", response_model=list[StockOut])
async def get_sw_level1_stocks(level1_code: str, db: DbDep) -> list[StockOut]:
    if await market_service.get_sw_level1(level1_code) is None:
        raise not_found_response("SW level1", level1_code)
    symbols = await market_service.list_symbols_by_level1(level1_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get("/sw-industry/{level1_code}/{level2_code}/stocks", response_model=list[StockOut])
async def get_sw_level2_stocks(level1_code: str, level2_code: str, db: DbDep) -> list[StockOut]:
    if await market_service.get_sw_level2(level1_code, level2_code) is None:
        raise not_found_response("SW level2", f"{level1_code}/{level2_code}")
    symbols = await market_service.list_symbols_by_level2(level1_code, level2_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/{level3_code}/stocks",
    response_model=list[StockOut],
)
async def get_sw_level3_stocks(
    level1_code: str, level2_code: str, level3_code: str, db: DbDep
) -> list[StockOut]:
    if await market_service.get_sw_level3(level1_code, level2_code, level3_code) is None:
        raise not_found_response("SW level3", f"{level1_code}/{level2_code}/{level3_code}")
    symbols = await market_service.list_symbols_by_level3(level1_code, level2_code, level3_code)
    return await market_service.list_stocks_by_symbols(db, symbols)
