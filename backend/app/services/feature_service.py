"""Feature service: radar chart and feature history."""

import logging
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import feature_repo, stock_repo
from app.schemas.feature import RadarAxis, RadarChartData, StockFeatureOut

logger = logging.getLogger(__name__)

_RADAR_AXES = [
    ("total_return", "累计收益"),
    ("annual_volatility", "年化波动率"),
    ("max_drawdown", "最大回撤"),
    ("trend_slope", "趋势斜率"),
    ("avg_volume", "平均成交量"),
]


async def get_radar_data(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
    window_days: int = 60,
) -> RadarChartData | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    cache_key = f"feature:radar:{exchange}:{symbol}:{window_days}"
    cached = await cache.get(cache_key)
    if cached:
        return RadarChartData(**cached)

    feature = await feature_repo.get_latest_features(db, stock.id, window_days)
    if feature is None:
        return None

    axes = [
        RadarAxis(name=label, value=getattr(feature, field))
        for field, label in _RADAR_AXES
    ]
    out = RadarChartData(
        symbol=symbol,
        name=stock.name,
        exchange=exchange,
        asof_date=feature.asof_date,
        window_days=window_days,
        axes=axes,
    )
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=600)
    return out


async def get_feature_history(
    db: AsyncSession,
    cache: CacheClient,
    exchange: str,
    symbol: str,
    window_days: int = 60,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[StockFeatureOut] | None:
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        return None

    features = await feature_repo.get_feature_history(
        db, stock.id, window_days, start_date, end_date
    )
    return [StockFeatureOut.model_validate(f) for f in features]
