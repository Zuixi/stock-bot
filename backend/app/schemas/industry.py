"""Industry research workbench schemas."""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class MetricDelta(BaseModel):
    pct: float | None
    direction: str  # up | down | flat
    label: str = "环比"


class MetricLatestOut(BaseModel):
    metric_key: str
    name: str
    value: float | None = None
    unit: str | None = None
    tier: str = "manual"
    source: str | None = None
    freq: str | None = None
    period: date | None = None
    delta: MetricDelta | None = None
    warn: str | None = None
    warn_severity: str | None = None
    spark: list[float] | None = None
    description: str = ""


class ReferenceOut(BaseModel):
    label: str
    value: float
    note: str | None = None
    effective_from: date


class TrendSeriesOut(BaseModel):
    periods: list[date]
    series: dict[str, list[float | None]]
    reference: ReferenceOut | None = None


class PhaseOut(BaseModel):
    key: str
    label: str
    desc: str
    active: bool = False


class PositionSliceOut(BaseModel):
    name: str
    role: str
    desc: str
    pct: int
    color: str


class CycleOut(BaseModel):
    phase: str
    phase_index: int
    phases: list[PhaseOut]
    reasons: list[str]
    basis: dict


class SignalOut(BaseModel):
    signal_type: str
    phase: str | None = None
    effective_date: date
    reason: str | None = None
    positions: list[PositionSliceOut] = []


class IndustryBriefOut(BaseModel):
    key: str
    name: str
    description: str
    sw_l3_codes: list[str]


class DashboardOut(BaseModel):
    industry: IndustryBriefOut
    as_of: date
    strip: list[MetricLatestOut]
    quick_view: list[MetricLatestOut]
    trends: dict[str, TrendSeriesOut]
    cycle: CycleOut
    signal: SignalOut
    signal_history: list[SignalOut]


class IndustrySummaryOut(BaseModel):
    key: str
    name: str
    description: str
    sw_l3_codes: list[str]
    metric_total: int
    metric_with_data: int
    coverage: dict[str, bool]
    last_period: date | None = None


class MetricHistoryPointOut(BaseModel):
    period: date
    value: float | None
    source: str
    freq: str


class MetricHistoryOut(BaseModel):
    metric_key: str
    name: str
    unit: str | None
    freq: str
    tier: str
    points: list[MetricHistoryPointOut]


class MetricBatchItem(BaseModel):
    metric_key: str
    period: date
    value: float
    source: str = "manual"
    freq: str | None = None
    unit: str | None = None
    stock_id: int | None = Field(default=None, ge=1, description="公司级指标携带 stocks.id")


class MetricBatchRequest(BaseModel):
    items: list[MetricBatchItem] = Field(min_length=1, max_length=5000)
    recompute_derived: bool = False


class MetricBatchResponse(BaseModel):
    upserted: int
    skipped_unknown_metric: list[str] = []
