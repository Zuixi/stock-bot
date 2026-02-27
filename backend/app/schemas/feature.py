"""Feature engineering request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel


class StockFeatureOut(BaseModel):
    stock_id: int
    asof_date: date
    window_days: int
    total_return: float | None
    return_percentile: float | None
    annual_volatility: float | None
    max_drawdown: float | None
    downside_vol: float | None
    trend_slope: float | None
    trend_r2: float | None
    ma_bullish: bool | None
    trend_reversals: int | None
    avg_volume: float | None
    volume_volatility: float | None
    created_at: datetime

    model_config = {"from_attributes": True}


class RadarAxis(BaseModel):
    name: str
    value: float | None
    percentile: float | None = None


class RadarChartData(BaseModel):
    symbol: str
    name: str
    asof_date: date
    window_days: int
    axes: list[RadarAxis]
