"""连板梯队与市场情绪响应模型（字段名与 docs/design/limit-up-sentiment.md §5 对齐）。

`source` 双口径（Task 11）：

- `local_calc`：本地口径（权威、可回放、写入 `market_sentiment_daily`）。
- `eastmoney_intraday`：盘中口径（仅今日，由东财涨停池兜底，**不写**派生表）。

盘中与收盘的**同构**由 `intraday_sentiment_service.build_intraday_snapshot` 保证：
消费方用同一份 `LimitUpLadderOut` / `SectorLimitUpOut` / `YesterdayLimitUpOut` 解析
路径同时吃下盘中与收盘两份快照。盘中无 SW L3 映射 → `SectorLimitUpItemOut.l3_*` /
`l1_*` 全部 `None`；盘中无窗口语义 → `LadderStockOut.days_span / boards_in_window /
missing_days` 全部 `None`；盘中无"昨日→今日"语义 → `YesterdayLimitUpOut.items = []`。
`as_of_label` 用于前端徽标（收盘="收盘"、盘中="盘中 HH:MM"、回落="盘中不可用，已回落收盘"）。
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel

from app.schemas.market import AsOfQuality

# source 双口径——见模块 docstring。盘中"source=eastmoney_intraday" 由 Task 11 引入；
# `local_calc` 是既有口径，保留兼容。
SentimentSource = Literal["local_calc", "eastmoney_intraday"]


class LadderStockOut(BaseModel):
    symbol: str
    name: str
    streak: int
    # 收盘=窗口语义给出 int；盘中=无窗口语义给 None。
    days_span: int | None = None
    boards_in_window: int | None = None
    missing_days: int | None = None
    sw_l1_name: str | None = None
    sw_l3_name: str | None = None
    seal_time: str | None = None  # 本地路径恒 None（前端渲染 --）
    seal_fund: float | None = None
    break_count: int | None = None


class EchelonOut(BaseModel):
    streak: int
    label: str
    stocks: list[LadderStockOut]


class SentimentKpisOut(BaseModel):
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    yzt_avg_pct: float | None = None
    yzt_avg_open_premium: float | None = None
    yzt_n: int = 0
    promo_1to2: float | None = None
    promo_1to2_n: int = 0
    promo_1to2_noisy: bool = True
    promo_2to3: float | None = None
    promo_2to3_n: int = 0
    promo_2to3_noisy: bool = True
    max_streak: int = 0


class LimitUpLadderOut(BaseModel):
    as_of: date | None
    as_of_prev: date | None
    as_of_quality: AsOfQuality = "partial"
    source: SentimentSource
    limits_present: bool
    is_partial: bool
    sw_coverage: float | None = None
    lookback: int
    degraded_reason: str | None = None
    kpis: SentimentKpisOut | None = None
    echelons: list[EchelonOut] = []
    # 收盘路径不设、默认 None（序列化时随 Pydantic 默认 include None 出现 null）；
    # 盘中路径填 "盘中 HH:MM"；回落填 "盘中不可用，已回落收盘"。
    as_of_label: str | None = None


class SectorLimitUpItemOut(BaseModel):
    # 收盘路径：SW L3/L1 必须非空（按 limit_up_calculator.sector_ladder 逻辑）。
    # 盘中路径：东财 hybk 体系不映射 SW，l3_*/l1_* 全 None。已在 Task 11 拓宽。
    l3_code: str | None = None
    l3_name: str | None = None
    l1_code: str | None = None
    l1_name: str | None = None
    max_streak: int
    leader_symbol: str
    leader_name: str | None = None
    leader_streak: int
    zt_count: int


class SectorLimitUpOut(BaseModel):
    as_of: date | None
    as_of_quality: AsOfQuality = "partial"
    source: SentimentSource
    degraded_reason: str | None = None
    unclassified_count: int = 0
    items: list[SectorLimitUpItemOut] = []
    as_of_label: str | None = None


class YesterdayLimitUpItemOut(BaseModel):
    symbol: str
    name: str
    prev_streak: int
    today_pct: float | None = None
    today_open_premium: float | None = None
    today_streak: int | None = None
    is_lu: bool = False
    touched: bool = False
    broken: bool = False
    suspended: bool = False
    missing_days: int = 0
    sw_l3_name: str | None = None


class YesterdayLimitUpOut(BaseModel):
    as_of: date | None
    as_of_prev: date | None
    as_of_quality: AsOfQuality = "partial"
    source: SentimentSource
    degraded_reason: str | None = None
    kpis: dict[str, Any] = {}
    items: list[YesterdayLimitUpItemOut] = []
    as_of_label: str | None = None


class SentimentCalendarPointOut(BaseModel):
    trade_date: date
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    yzt_avg_pct: float | None = None
    promo_1to2: float | None = None
    promo_2to3: float | None = None
    max_streak: int = 0
