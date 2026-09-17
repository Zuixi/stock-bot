"""Market-data face response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

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
    lead_stock_name: str | None = None  # 主力净流入最大股
    lead_stock_code: str | None = None
    lead_stock_pct: float | None = None  # %


class SectorMoneyflowListOut(BaseModel):
    """板块资金流列表 envelope：`as_of` = 该快照表实际持有的最近日。"""

    as_of: str | None = None  # ISO 日期；表空时 None
    stale_days: int | None = None  # 自然日差（今天 - as_of）；表空时 None
    items: list[SectorMoneyflowOut] = Field(default_factory=list)


class MarketMoneyflowTotalOut(BaseModel):
    amount: float | None = None  # 成交额，元
    main_net: float | None = None  # 元
    super_large_net: float | None = None  # 元
    large_net: float | None = None  # 元
    mid_net: float | None = None  # 元
    small_net: float | None = None  # 元


class MarketMoneyflowMarketOut(BaseModel):
    code: str | None = None
    name: str | None = None
    main_net: float | None = None  # 元
    super_large_net: float | None = None
    large_net: float | None = None
    mid_net: float | None = None
    small_net: float | None = None
    main_ratio: float | None = None  # %


class MarketMoneyflowDayOut(BaseModel):
    date: str
    main_net: float | None = None  # 元
    super_large_net: float | None = None
    large_net: float | None = None
    mid_net: float | None = None
    small_net: float | None = None
    main_ratio: float | None = None  # %
    close: float | None = None
    pct_change: float | None = None  # %
    amount: float | None = None  # 元


class MarketMoneyflowTodayOut(BaseModel):
    total: MarketMoneyflowTotalOut
    markets: list[MarketMoneyflowMarketOut] = Field(default_factory=list)


class MarketMoneyflowOut(BaseModel):
    today: MarketMoneyflowTodayOut | None = None
    history: list[MarketMoneyflowDayOut] = Field(default_factory=list)
    history_as_of: str | None = None  # 历史表最近日 ISO；表空时 None
    history_stale_days: int | None = None  # 自然日差；表空时 None


class NorthboundPointOut(BaseModel):
    date: str
    net_amount: float | None = None  # 万元


class NorthboundSeriesOut(BaseModel):
    """北向净流入序列 envelope；上游停更时由 `source_status` 显式标注。"""

    as_of: str | None = None  # ISO 日期（该表最近日）；表空时 None
    stale_days: int | None = None  # 自然日差（今天 - as_of）；表空时 None
    source_status: Literal["live", "stale", "discontinued"] = "discontinued"
    items: list[NorthboundPointOut] = Field(default_factory=list)


class DragonTigerOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    close: float | None = None
    pct_change: float | None = None
    turnover_rate: float | None = None
    amount: float | None = None  # 元
    l_buy: float | None = None  # 元
    l_sell: float | None = None  # 元
    l_amount: float | None = None  # 元
    net_amount: float | None = None  # 元
    reason: str


class BlockTradeOut(BaseModel):
    trade_date: str
    ts_code: str
    symbol: str
    name: str | None = None
    price: float | None = None  # 元
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
    vol: float | None = None  # 股
    amount: float | None = None  # 元


class AnnouncementOut(BaseModel):
    announcement_id: str
    sec_code: str
    sec_name: str | None = None
    title: str
    announce_time: str  # ISO datetime str
    category: str  # report | event
    pdf_url: str | None = None
