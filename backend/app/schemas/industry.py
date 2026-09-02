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
    data_source: str = "mock"  # settings.industry_data_source，前端据此展示演示标签
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
    # 列表卡片状态行（P6）：最新信号（从未 ingest 评估过的行业为全 None）
    phase: str | None = None       # 周期阶段 key（prosperity/recession/depression/recovery）
    signal_type: str | None = None  # 买入/卖出/关注/空仓
    signal_date: date | None = None


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
    derived_upserted: int = 0
    skipped_unknown_metric: list[str] = []
    skipped_invalid_source: list[str] = []


# ── 标的分析（P5）：成分股对比表 ───────────────────────────────────────

class CompanyColumnOut(BaseModel):
    """对比表列定义：固定行情列 + registry 下发的公司指标列（前端零改动扩展）。"""

    key: str                     # 行取值键：固定列同名字段，公司指标列读 row.metrics[key]
    label: str
    unit: str | None = None
    numeric: bool = True         # 前端右对齐 + 排序
    tier: str | None = None      # 公司指标列的数据源层级徽章


class CompanyRowOut(BaseModel):
    symbol: str
    name: str
    latest_price: float | None = None
    total_mv_yi: float | None = None   # 亿元（daily_basic.total_mv 万元 / 1e4）
    pe_ttm: float | None = None
    pb: float | None = None
    has_company_data: bool = False
    metrics: dict[str, float | None] = {}   # metric_key → latest 公司指标值（含 mcap_per_head）


class IndustryCompaniesOut(BaseModel):
    industry: IndustryBriefOut
    columns: list[CompanyColumnOut]
    rows: list[CompanyRowOut]


# ── 行情面（P5）：ETF / 可转债日线 ────────────────────────────────────

class SecurityDailyPointOut(BaseModel):
    """一天的 OHLCV（TuShare fund_daily/cb_daily 原样口径，volume 手/张、amount 千元）。"""

    trade_date: date
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    pre_close: float | None = None
    volume: float | None = None
    amount: float | None = None


class SecuritySeriesOut(BaseModel):
    """一个标的：latest 一行 + 最近 N 日序列（sparkline 用）+ 最新涨跌幅。"""

    ts_code: str
    name: str | None = None
    latest: SecurityDailyPointOut | None = None
    change_pct: float | None = None  # (close - pre_close) / pre_close × 100
    series: list[SecurityDailyPointOut] = []


class IndustrySecuritiesOut(BaseModel):
    type: str  # etf | cb
    codes: list[SecuritySeriesOut]


# ── 知识库（P6）：机构图谱 / 权威性原则 / 思维导图 ─────────────────────

class KnowledgeOrgOut(BaseModel):
    """机构条目：分组 + 权威性徽章 tier（复用 official/highfreq/calc/manual 五级）。"""

    name: str
    group: str            # 官方 | 协会 | 数据平台 | 期货
    tier: str             # official | highfreq | calc | manual
    desc: str = ""
    urls: list[str] = []


class KnowledgePrincipleOut(BaseModel):
    title: str
    items: list[str]


class IndustryKnowledgeOut(BaseModel):
    org: list[KnowledgeOrgOut] = []
    principle: KnowledgePrincipleOut | None = None
    mindmap: dict | None = None  # EChart tree 直用 {name, children}（透传）
