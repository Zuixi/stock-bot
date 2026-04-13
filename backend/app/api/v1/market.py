"""Market endpoints for dashboard data."""

from typing import Literal

from fastapi import APIRouter, Query

from app.api.deps import DbDep
from app.core.exceptions import not_found_response
from app.schemas.stock import StockOut
from app.services import market_service

router = APIRouter()


@router.get("/indices", response_model=list[dict])
async def list_market_indices() -> list[dict]:
    return market_service.list_market_indices()


@router.get("/distribution", response_model=list[dict])
async def get_distribution() -> list[dict]:
    return market_service.get_distribution()


@router.get("/sectors", response_model=list[dict])
async def get_sectors() -> list[dict]:
    return market_service.get_sectors()


@router.get("/capital-flow", response_model=list[dict])
async def get_capital_flow() -> list[dict]:
    return market_service.get_capital_flow()


@router.get("/hot-boards", response_model=list[dict])
async def get_hot_boards(
    category: Literal["industry", "concept", "region"] = Query(default="industry"),
) -> list[dict]:
    return market_service.get_hot_boards(category)


@router.get("/sw-industry/tree", response_model=list[dict])
async def get_sw_industry_tree() -> list[dict]:
    return market_service.get_sw_industry_tree()


@router.get("/sw-industry/{level1_code}/stocks", response_model=list[StockOut])
async def get_sw_level1_stocks(level1_code: str, db: DbDep) -> list[StockOut]:
    if market_service.get_sw_level1(level1_code) is None:
        raise not_found_response("SW level1", level1_code)
    symbols = market_service.list_symbols_by_level1(level1_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get("/sw-industry/{level1_code}/{level2_code}/stocks", response_model=list[StockOut])
async def get_sw_level2_stocks(level1_code: str, level2_code: str, db: DbDep) -> list[StockOut]:
    if market_service.get_sw_level2(level1_code, level2_code) is None:
        raise not_found_response("SW level2", f"{level1_code}/{level2_code}")
    symbols = market_service.list_symbols_by_level2(level1_code, level2_code)
    return await market_service.list_stocks_by_symbols(db, symbols)


@router.get(
    "/sw-industry/{level1_code}/{level2_code}/{level3_code}/stocks",
    response_model=list[StockOut],
)
async def get_sw_level3_stocks(
    level1_code: str, level2_code: str, level3_code: str, db: DbDep
) -> list[StockOut]:
    if market_service.get_sw_level3(level1_code, level2_code, level3_code) is None:
        raise not_found_response("SW level3", f"{level1_code}/{level2_code}/{level3_code}")
    symbols = market_service.list_symbols_by_level3(level1_code, level2_code, level3_code)
    return await market_service.list_stocks_by_symbols(db, symbols)
