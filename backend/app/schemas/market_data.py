"""Market-data face response schemas."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class GlobalIndexCardOut(BaseModel):
    ts_code: str
    name: str
    market: str
    region: str
    price: float | None = None
    change: float | None = None
    pct_change: float | None = None
    spark: list[float] = Field(default_factory=list)
    updated_at: datetime
    source: str
