"""Daily basic indicator response schema.

Maps to the frontend StockRecord fields: marketCap, circulatingCap, pe, pb, etc.
"""

from datetime import date, datetime

from pydantic import BaseModel


class DailyBasicOut(BaseModel):
    trade_date: date
    close: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    ps: float | None = None
    ps_ttm: float | None = None
    dv_ratio: float | None = None
    dv_ttm: float | None = None
    total_share: float | None = None
    float_share: float | None = None
    free_share: float | None = None
    total_mv: float | None = None
    circ_mv: float | None = None

    model_config = {"from_attributes": True}


class DailyBasicLatestOut(BaseModel):
    """Latest daily_basic snapshot for a single stock — matches frontend FundamentalCards."""

    symbol: str
    name: str
    exchange: str
    trade_date: date
    # Valuation
    pe: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    ps: float | None = None
    ps_ttm: float | None = None
    # Market cap (in 10,000 CNY from TuShare — keep raw, frontend converts)
    total_mv: float | None = None
    circ_mv: float | None = None
    # Activity
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    # Dividend
    dv_ratio: float | None = None
    dv_ttm: float | None = None
    # Shares
    total_share: float | None = None
    float_share: float | None = None
    # Timestamp
    updated_at: datetime | None = None


class DailyBasicListResponse(BaseModel):
    symbol: str
    name: str
    exchange: str
    data: list[DailyBasicOut]
