"""Feature endpoints: radar chart and feature history."""

from datetime import date

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.feature import RadarChartData, StockFeatureOut
from app.services import feature_service

router = APIRouter()


@router.get("/{symbol}", response_model=RadarChartData)
async def get_radar(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    window_days: int = Query(default=60, description="Feature window in trading days"),
) -> RadarChartData:
    result = await feature_service.get_radar_data(db, cache, symbol, window_days)
    if result is None:
        raise not_found_response("Feature", symbol)
    return result


@router.get("/{symbol}/history", response_model=list[StockFeatureOut])
async def get_feature_history(
    symbol: str,
    db: DbDep,
    cache: CacheDep,
    window_days: int = Query(default=60),
    start: date | None = Query(None),
    end: date | None = Query(None),
) -> list[StockFeatureOut]:
    result = await feature_service.get_feature_history(
        db, cache, symbol, window_days, start, end
    )
    if result is None:
        raise not_found_response("Stock", symbol)
    return result
