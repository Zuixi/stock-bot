"""Pydantic schemas for SSE index snapshot endpoints."""

from datetime import date, datetime

from pydantic import BaseModel


class SseSnapshotOut(BaseModel):
    code: str
    name: str
    last: float
    prev_close: float | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    chg_rate: float | None = None
    collect_time: datetime
    trade_date: date

    model_config = {"from_attributes": True}


class SseIntradayPoint(BaseModel):
    time: str
    last: float
    chg_rate: float | None = None


class SseIntradayResponse(BaseModel):
    code: str
    name: str
    trade_date: date
    data: list[SseIntradayPoint]


class BackfillRequest(BaseModel):
    start_date: date
    end_date: date


class BackfillResponse(BaseModel):
    message: str
    start_date: date
    end_date: date
