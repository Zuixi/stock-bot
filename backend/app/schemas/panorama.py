"""A股全景读面（`GET /market/panorama`）响应模型。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.market import AsOfQuality

StructureState = Literal["broad_rally", "structured", "broad_decline", "concentration"]
StructureTone = Literal["hot", "neutral", "cold", "extreme"]


class PanoramaWindowOut(BaseModel):
    days: int
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    samples: int = 0

    model_config = {"populate_by_name": True}


class PanoramaBreadthPointOut(BaseModel):
    trade_date: date
    up_count: int
    down_count: int
    flat_count: int
    limit_up_count: int | None = None
    limit_down_count: int | None = None
    median_pct: float | None = None
    mean_pct: float | None = None
    #: 千元（与 daily_quotes.amount 同单位）
    amount: float | None = None
    #: up / (up + down + flat)；口径 = 上涨 ≥1%（与涨跌分布卡一致）
    up_ratio: float | None = None


class PanoramaBreadthOut(BaseModel):
    latest: PanoramaBreadthPointOut | None = None
    previous: PanoramaBreadthPointOut | None = None
    series: list[PanoramaBreadthPointOut] = []


class PanoramaBucketOut(BaseModel):
    bucket: str
    count: int


class PanoramaIndustryOut(BaseModel):
    industry_code: str
    industry_name: str
    pct_chg: float | None = None
    up_count: int
    down_count: int
    flat_count: int
    limit_up_count: int | None = None
    amount: float | None = None
    breadth: float | None = None
    rank: int
    #: 正 = 排名上升（昨天第 10、今天第 7 → +3）
    rank_delta: int | None = None
    #: C 类归属：current_sw_snapshot
    attribution: str = "current_sw_snapshot"


class PanoramaStructurePointOut(BaseModel):
    trade_date: date
    up_ratio: float | None = None
    top3_share: float | None = None
    hhi: float | None = None
    dispersion: float | None = None
    state: StructureState
    label: str
    tone: StructureTone


class PanoramaStructureOut(BaseModel):
    current: PanoramaStructurePointOut | None = None
    series: list[PanoramaStructurePointOut] = []


class PanoramaFlowItemOut(BaseModel):
    board_code: str
    board_name: str | None = None
    pct_change: float | None = None
    #: 元
    main_net_inflow: float | None = None
    #: main_net_inflow / amount（板块成交额）
    net_ratio: float | None = None


class PanoramaFlowsOut(BaseModel):
    as_of: date
    #: 最早完整日；None = 还没有任何 eod_full
    available_from: date | None = None
    top_in: list[PanoramaFlowItemOut] = []
    top_out: list[PanoramaFlowItemOut] = []


class PanoramaFreshnessOut(BaseModel):
    breadth_as_of: date | None = None
    industry_as_of: date | None = None
    flow_as_of: date | None = None
    flow_available_from: date | None = None


class MarketPanoramaOut(BaseModel):
    as_of: date | None = None
    expected_as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    stale: bool | None = None
    lag_days: int | None = None
    window: PanoramaWindowOut
    breadth: PanoramaBreadthOut
    buckets: list[PanoramaBucketOut] = []
    industries: list[PanoramaIndustryOut] = []
    structure: PanoramaStructureOut
    flows: PanoramaFlowsOut | None = None
    freshness: PanoramaFreshnessOut
