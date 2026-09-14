"""连板梯队与市场情绪响应模型（字段名与 docs/design/limit-up-sentiment.md §5 对齐）。

`source` 只允许 `local_calc`：本地口径是唯一权威、可回放的路径（决策 3）。
东财 `hybk` 是**东财板块**口径，填不进申万 L3，所以 Web 不产出完整 payload，
只给封板时间/封单/炸板次数三个增强字段（Task 6）；不要给它编一个 source="web"。
"""

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel


class LadderStockOut(BaseModel):
    symbol: str
    name: str
    streak: int
    days_span: int
    boards_in_window: int
    missing_days: int
    sw_l1_name: str | None = None
    sw_l3_name: str | None = None
    seal_time: str | None = None      # 本地路径恒 None（前端渲染 --）
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
    source: Literal["local_calc"]
    limits_present: bool
    is_partial: bool
    sw_coverage: float | None = None
    lookback: int
    degraded_reason: str | None = None
    kpis: SentimentKpisOut | None = None
    echelons: list[EchelonOut] = []


class SectorLimitUpItemOut(BaseModel):
    l3_code: str
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
    source: Literal["local_calc"]
    degraded_reason: str | None = None
    unclassified_count: int = 0
    items: list[SectorLimitUpItemOut] = []


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
    source: Literal["local_calc"]
    degraded_reason: str | None = None
    kpis: dict[str, Any] = {}
    items: list[YesterdayLimitUpItemOut] = []


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
