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


class DragonTigerOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    close: float | None = None
    pct_change: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None      # 元
    l_buy: float | None = None       # 元
    l_sell: float | None = None      # 元
    l_amount: float | None = None    # 元
    net_amount: float | None = None  # 元
    reason: str


class BlockTradeOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    price: float | None = None   # 元
    volume: float | None = None  # 万股
    amount: float | None = None  # 万元
    buyer: str | None = None
    seller: str | None = None


class ShareFloatOut(BaseModel):
    ann_date: str | None = None
    float_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    float_share: float | None = None  # 万股
    float_ratio: float | None = None  # %
    holder_name: str | None = None
    share_type: str | None = None


class RepurchaseOut(BaseModel):
    ann_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    proc: str
    end_date: str | None = None
    exp_date: str | None = None
    vol: float | None = None     # 股
    amount: float | None = None  # 元
