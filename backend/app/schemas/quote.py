"""Quote request/response schemas."""

from datetime import date, datetime

from pydantic import BaseModel


class DailyQuoteOut(BaseModel):
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None
    amount: float | None
    adj_factor: float | None

    model_config = {"from_attributes": True}


class KlineResponse(BaseModel):
    symbol: str
    name: str
    data: list[DailyQuoteOut]


class LatestQuoteOut(BaseModel):
    symbol: str
    name: str
    trade_date: date
    close: float
    volume: int | None
    amount: float | None
    updated_at: datetime | None = None
