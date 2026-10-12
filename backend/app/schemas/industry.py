"""Industry research workbench schemas."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, Field, field_validator

# PIT 时点口径：中国市场的"实际可得日"按 Asia/Shanghai 日历判定（timestamptz 的 .date()
# 取的是 UTC 日期，北京 00:30 发布会被算成前一天 = 回测偷看未来）。
# 时区常量单点在 service 层的 `_CN_TZ`（此处不再重复声明，避免两处口径漂移）。


class MetricDelta(BaseModel):
    pct: float | None
    direction: str  # up | down | flat
    label: str = "环比"


class MetricLatestOut(BaseModel):
    """一个 scope 的一个指标最新值。

    四徽章字段（spec §S1.2）：``source`` / ``as_of`` / ``freq`` / ``caliber_version``。
    ``as_of`` = 该值**实际可得时点**（PIT）；``published_at`` 为 NULL 的行情类序列
    （"发布即连续可得"）由 ``as_of_source`` 显式标注回退，**绝不静默**（ADR 0011:26-27）：
    ``as_of_source == "published_at"`` → 真 PIT；``"period"`` → 回退值，规则/回测路径
    （S2.2）应要求前者。``as_of`` 与 ``period`` 均为 Asia/Shanghai 日历日。
    ``caliber_version_registered`` = registry 声明的当前口径；与 ``caliber_version``
    （行上实际口径）不一致即上游口径断点。
    """

    metric_key: str
    name: str
    value: float | None = None
    unit: str | None = None
    tier: str = "manual"
    source: str | None = None
    freq: str | None = None
    period: date | None = None
    as_of: date | None = None  # PIT 徽章：published_at 的上海日历日；无则回退 period
    # 回退判别式（Ruling 34）：published_at = 真 PIT；period = 回退，消费方必须能分辨
    as_of_source: str | None = None  # published_at | period
    published_at: datetime | None = None  # PIT 原值（NULL = 未登记）
    caliber_version: str | None = None  # 口径版本徽章（行上实际值）
    caliber_version_registered: str | None = None  # registry 声明的当前口径
    scope_key: str | None = None  # 该行所属 scope（industry_key 泛化后的读面别名）
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
    phase: str | None = None  # 周期阶段 key（prosperity/recession/depression/recovery）
    signal_type: str | None = None  # 买入/卖出/关注/空仓
    signal_date: date | None = None


class MetricHistoryPointOut(BaseModel):
    period: date
    value: float | None
    source: str
    freq: str
    as_of: date | None = None  # PIT：published_at 的上海日历日，NULL 时回退 period
    as_of_source: str | None = None  # 回退判别式（Ruling 34）：published_at | period
    published_at: datetime | None = None
    caliber_version: str | None = None


class MetricHistoryOut(BaseModel):
    metric_key: str
    name: str
    unit: str | None
    freq: str
    tier: str
    points: list[MetricHistoryPointOut]


# ── 通用指标读面（spec §S1.2：GET /api/v1/metrics/latest | /series）─────────
# 与行业工作台的 MetricLatestOut 同形状（四徽章 + registry 元数据），只额外带
# scope_key，使同一读面同时服务行业 scope 与后续情绪/宏观的非行业 scope。


class MetricSeriesOut(BaseModel):
    """升序序列 + 四徽章（Redis 缓存）。

    ``range`` 是**真实时间窗**：``period_from`` 给出窗口下界（含），返回的点全部
    ``period >= period_from``；``limit`` 只是防御性行数上界（防病态数据拉爆），
    打满时 ``truncated=True``。
    ``range`` 回传的是**规范化文本**（按天数：``1y`` → ``"366d"``、``3m`` → ``"93d"``、
    ``all`` → ``"all"``），不是请求字面量 —— 同一窗口的不同写法必须回同一个值。
    """

    scope_key: str
    metric_key: str
    name: str
    unit: str | None = None
    tier: str = "manual"
    freq: str
    source: str | None = None  # 采纳的源（registry 优先级最高者）
    source_mismatch: bool = False  # True = 库里源的都不在 registry 优先级表里（发出而非静默丢弃）
    caliber_version: str | None = None
    caliber_version_registered: str | None = None
    range: str
    period_from: date | None = None  # 窗口下界（含）；"all" 为 None
    limit: int
    truncated: bool = False  # 命中 limit 上界（还有更早的点未返回）
    points: list[MetricHistoryPointOut] = []


class MetricBatchItem(BaseModel):
    metric_key: str
    period: date
    value: float
    source: str = "manual"
    freq: str | None = None
    unit: str | None = None
    stock_id: int | None = Field(default=None, ge=1, description="公司级指标携带 stocks.id")
    # PIT：人工/CSV 通道可选填写"实际可得时点"（公告日/CSV 落库日）；不填即 NULL 待登
    published_at: datetime | None = None
    # 口径版本：仅当本次导入的上游口径与既有不同才填（留断点）
    caliber_version: str | None = None

    @field_validator("published_at")
    @classmethod
    def _published_at_must_be_aware(cls, v: datetime | None) -> datetime | None:
        """PIT 时点必须带时区：naive datetime 的"实际可得日"是歧义的（F3）。

        拒绝而不是猜（猜错即回测偷看未来）。调用方应传 ISO 8601 带偏移，
        如 ``2026-10-09T00:30:00+08:00``。
        """
        if v is not None and v.tzinfo is None:
            raise ValueError(
                "published_at 必须带时区（如 2026-10-09T00:30:00+08:00）："
                "naive 时点的实际可得日不可判定"
            )
        return v


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

    key: str  # 行取值键：固定列同名字段，公司指标列读 row.metrics[key]
    label: str
    unit: str | None = None
    numeric: bool = True  # 前端右对齐 + 排序
    tier: str | None = None  # 公司指标列的数据源层级徽章


class CompanyRowOut(BaseModel):
    symbol: str
    name: str
    latest_price: float | None = None
    total_mv_yi: float | None = None  # 亿元（daily_basic.total_mv 万元 / 1e4）
    pe_ttm: float | None = None
    pb: float | None = None
    has_company_data: bool = False
    metrics: dict[str, float | None] = {}  # metric_key → latest 公司指标值（含 mcap_per_head）


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
    group: str  # 官方 | 协会 | 数据平台 | 期货
    tier: str  # official | highfreq | calc | manual
    desc: str = ""
    urls: list[str] = []


class KnowledgePrincipleOut(BaseModel):
    title: str
    items: list[str]


class IndustryKnowledgeOut(BaseModel):
    org: list[KnowledgeOrgOut] = []
    principle: KnowledgePrincipleOut | None = None
    mindmap: dict | None = None  # EChart tree 直用 {name, children}（透传）
