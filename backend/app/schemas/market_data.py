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


class SectorMoneyflowOut(BaseModel):
    board_code: str
    board_name: str | None = None
    pct_change: float | None = None
    main_net_inflow: float | None = None  # 元
    super_large_net: float | None = None  # 元
    large_net: float | None = None  # 元
    main_net_ratio: float | None = None  # %
    up_count: int | None = None
    down_count: int | None = None


class NorthboundPointOut(BaseModel):
    date: str
    net_amount: float | None = None  # 万元
