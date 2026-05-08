"""Stock + quotes + features endpoints.

Routes include exchange metadata under ``/api/v1/exchanges``,
cross-exchange stock listing under ``/api/v1/exchanges/stocks``,
and per-exchange stock resources under ``/api/v1/exchanges/{exchange}/stocks``.
"""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.common import PageParams, PagedResponse
from app.schemas.daily_basic import DailyBasicLatestOut, DailyBasicListResponse
from app.schemas.feature import RadarChartData, StockFeatureOut
from app.schemas.quote import KlineResponse, LatestQuoteOut
from app.schemas.stock import StockOut, StockListParams
from app.services import (
    daily_basic_service,
    feature_service,
    quote_service,
    stock_service,
    stock_tag_service,
    user_tag_service,
)

router = APIRouter()
stocks_router = APIRouter()


# ── /api/v1/exchanges ────────────────────────────────────────────────────────

@router.get("", response_model=list[dict])
def list_exchanges() -> list[dict]:
    """List all supported exchanges with stock counts."""
    raw = stock_service.list_exchanges()
    return [e.model_dump() for e in raw]


@router.get("/categories", response_model=list[dict])
async def list_categories(
    db: DbDep,
    cache: CacheDep,
    exchange: str | None = None,
) -> list[dict]:
    """List stock categories, optionally filtered by exchange."""
    rows = await stock_service.list_categories(db, cache, exchange)
    return [r.model_dump() for r in rows]


@router.get("/stocks", response_model=PagedResponse[StockOut])
async def list_stocks_all_exchanges(
    db: DbDep,
    cache: CacheDep,
    exchange: str | None = None,
    category: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PagedResponse[StockOut]:
    """List stocks across all exchanges (optionally filtered by exchange)."""
    params = StockListParams(
        exchange=exchange, category=category, keyword=keyword
    )
    page_params = PageParams(page=page, page_size=page_size)
    items, total = await stock_service.list_stocks(db, cache, params, page_params)
    return PagedResponse.build(items=items, total=total, params=page_params)


# ── /api/v1/exchanges/{exchange}/stocks ──────────────────────────────────────

@stocks_router.get("", response_model=PagedResponse[StockOut])
async def list_stocks(
    exchange: str,
    db: DbDep,
    cache: CacheDep,
    category: str | None = None,
    keyword: str | None = None,
    page: int = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
) -> PagedResponse[StockOut]:
    params = StockListParams(
        exchange=exchange, category=category, keyword=keyword
    )
    page_params = PageParams(page=page, page_size=page_size)
    items, total = await stock_service.list_stocks(db, cache, params, page_params)
    return PagedResponse.build(items=items, total=total, params=page_params)


@stocks_router.get("/{symbol}", response_model=StockOut)
async def get_stock(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
) -> StockOut:
    stock = await stock_service.get_stock(db, cache, exchange, symbol)
    if stock is None:
        raise not_found_response("Stock", f"{exchange}/{symbol}")
    return stock


# ── /api/v1/exchanges/{exchange}/stocks/{symbol}/quotes ──────────────────────

@stocks_router.get("/{symbol}/quotes/daily", response_model=KlineResponse)
async def get_kline(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    start: date | None = None,
    end: date | None = None,
) -> KlineResponse:
    result = await quote_service.get_kline(db, cache, exchange, symbol, start, end)
    if result is None:
        raise not_found_response("Stock", f"{exchange}/{symbol}")
    return result


@stocks_router.get("/{symbol}/quotes/latest", response_model=LatestQuoteOut)
async def get_latest_quote(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
) -> LatestQuoteOut:
    result = await quote_service.get_latest_quote(db, cache, exchange, symbol)
    if result is None:
        raise not_found_response("Quote", f"{exchange}/{symbol}")
    return result


# ── /api/v1/exchanges/{exchange}/stocks/{symbol}/daily-basic ────────────────

@stocks_router.get(
    "/{symbol}/daily-basic", response_model=DailyBasicListResponse,
)
async def get_daily_basic_history(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    start: date | None = None,
    end: date | None = None,
) -> DailyBasicListResponse:
    result = await daily_basic_service.get_daily_basic_history(
        db, cache, exchange, symbol, start, end,
    )
    if result is None:
        raise not_found_response("Stock", f"{exchange}/{symbol}")
    return result


@stocks_router.get(
    "/{symbol}/daily-basic/latest", response_model=DailyBasicLatestOut,
)
async def get_latest_daily_basic(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
) -> DailyBasicLatestOut:
    result = await daily_basic_service.get_latest_daily_basic(
        db, cache, exchange, symbol,
    )
    if result is None:
        raise not_found_response("DailyBasic", f"{exchange}/{symbol}")
    return result


# ── /api/v1/exchanges/{exchange}/stocks/{symbol}/features ───────────────────

@stocks_router.get("/{symbol}/features", response_model=list[StockFeatureOut])
async def get_feature_history(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    window_days: Annotated[int, Query(description="Feature window in trading days")] = 60,
    start: date | None = None,
    end: date | None = None,
) -> list[StockFeatureOut]:
    result = await feature_service.get_feature_history(
        db, cache, exchange, symbol, window_days, start, end
    )
    if result is None:
        raise not_found_response("Stock", f"{exchange}/{symbol}")
    return result


@stocks_router.get("/{symbol}/features/radar", response_model=RadarChartData)
async def get_radar(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    window_days: Annotated[int, Query(description="Feature window in trading days")] = 60,
) -> RadarChartData:
    result = await feature_service.get_radar_data(
        db, cache, exchange, symbol, window_days
    )
    if result is None:
        raise not_found_response("Feature", f"{exchange}/{symbol}")
    return result


# ── /api/v1/exchanges/{exchange}/stocks/{symbol}/sw-tags ─────────────────────

@stocks_router.get("/{symbol}/sw-tags", response_model=list[dict])
async def get_sw_tags(exchange: str, symbol: str, db: DbDep) -> list[dict]:
    """Get user-defined SW industry tags for a stock."""
    return await stock_tag_service.get_custom_sw_tags(db, symbol)


@stocks_router.put("/{symbol}/sw-tags", response_model=list[dict])
async def set_sw_tags(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    industry_codes: Annotated[list[str], Body(embed=True)],
) -> list[dict]:
    """Replace all custom SW industry tags for a stock."""
    try:
        result = await stock_tag_service.set_custom_sw_tags(db, symbol, industry_codes)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await cache.delete("market:sw-tree")
    return result


# ── /api/v1/exchanges/{exchange}/stocks/{symbol}/user-tags ─────────────────────

@stocks_router.get("/{symbol}/user-tags", response_model=list[dict])
async def get_user_tags(exchange: str, symbol: str, db: DbDep) -> list[dict]:
    """Get user-defined custom tags for a stock."""
    tags = await user_tag_service.get_stock_tags(db, symbol)
    return [t.model_dump(mode="json") for t in tags]


@stocks_router.post("/{symbol}/user-tags", response_model=dict)
async def add_user_tag(
    exchange: str,
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    tag_name: Annotated[str, Body(embed=True)],
) -> dict:
    """Add a user-defined tag to a stock."""
    try:
        tag = await user_tag_service.add_stock_tag(db, symbol, tag_name)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await db.commit()
    await cache.delete("tags:all")
    return tag.model_dump(mode="json")


@stocks_router.delete("/{symbol}/user-tags/{tag_name}")
async def remove_user_tag(
    exchange: str,
    symbol: str,
    tag_name: str,
    db: DbDep,
    cache: CacheDep,
) -> dict:
    """Remove a user-defined tag from a stock."""
    deleted = await user_tag_service.remove_stock_tag(db, symbol, tag_name)
    if not deleted:
        raise HTTPException(status_code=404, detail="Tag not found")
    await db.commit()
    await cache.delete("tags:all")
    return {"deleted": True}
