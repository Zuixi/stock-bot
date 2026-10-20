"""监管异动雷达响应模型（口径见 docs/design/regulatory-anomaly.md）。"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

Direction = Literal["up", "down"]
Status = Literal["triggered", "near", "watch"]
AsOfQuality = Literal["complete", "partial", "fallback", "requested"]


class NextTriggerOut(BaseModel):
    """明日触发价（3 日窗口 = 最近 2 个已知交易日 + 明日，假设基准指数明日持平）。"""

    price: float
    needed_pct: float
    partial_pct: float
    limit_pct: float
    feasible: bool


class RegulatoryItemOut(BaseModel):
    symbol: str
    name: str
    board: str
    is_st: bool
    direction: Direction
    status: Status
    close: float
    day_pct: float | None = None
    threshold_pct: float
    dev3_pct: float | None = None
    dev10_pct: float | None = None
    dev30_pct: float | None = None
    events_10d: int
    severe_flags: list[str] = []
    #: 连板数：只有 `market_limit_up_stock_daily` 当天有行时才有值（缺行 ≠ 0 板）
    streak: int | None = None
    #: 近 10 日 N 天 M 板（同源 facts；断板日 `streak=0` 但 `boards_in_window` 仍可 >1）
    days_span: int | None = None
    boards_in_window: int | None = None
    limit_up: bool = False
    limit_down: bool = False
    benchmark_code: str
    benchmark_name: str
    next_trigger: NextTriggerOut | None = None


class HistoryItemOut(BaseModel):
    """近 N 日「首次越过阈值」的一条。

    **不带 `threshold_pct`**：阈值按**今日**的板块/ST 快照算，拿来标注历史触发日
    就是拿今天的口径读过去（摘帽/戴帽会差 12% vs 20%）。要带就得有 PIT 名称表。
    """

    symbol: str
    name: str
    board: str
    direction: Direction
    dev3_pct: float


class HistoryDayOut(BaseModel):
    trade_date: date
    items: list[HistoryItemOut] = []


class TurnoverOnlyOut(BaseModel):
    """仅换手率口径达标的票（价格口径未达线，故不在 `items` 里）。"""

    symbol: str
    name: str
    board: str
    is_st: bool
    close: float
    day_pct: float | None = None
    #: 3 日累计换手率（%）
    turnover_3d_pct: float
    #: 3 日日均换手率（%）
    turnover_3d_avg_pct: float
    #: 前 5 日日均换手率（%）
    turnover_prev5_avg_pct: float
    #: 3 日均 / 前 5 日均 的倍数
    turnover_ratio: float


class RegulatorySummaryOut(BaseModel):
    up_triggered: int = 0
    up_near: int = 0
    down_triggered: int = 0
    down_near: int = 0
    severe: int = 0
    limit_up: int = 0
    limit_down: int = 0


class ThresholdOut(BaseModel):
    board: str
    exchange: str
    threshold_pct: float
    st_threshold_pct: float | None = None
    price_limit_pct: float
    st_price_limit_pct: float | None = None
    benchmark_code: str
    benchmark_name: str


class RegulatoryRadarOut(BaseModel):
    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    #: 自然日陈旧度（今天 − as_of）。全站 `FreshnessNote` 只看它 > 0 才加「N 天前」
    #: 徽标；节后第一天 / 上游停更时这是唯一能让人发现「日期不是今天」的强调。
    stale_days: int | None = None
    #: `no_quotes` / `not_a_trade_day` / `price_limits_missing` / `benchmark_missing`；
    #: 页面必须显式提示，不静默空表。`benchmark_missing` = 基准指数日线缺日：
    #: 个股行情在但 3 日偏离算不出，此时 KPI 全部为 0 **不代表**今天没有异动。
    degraded_reason: str | None = None
    summary: RegulatorySummaryOut = RegulatorySummaryOut()
    items: list[RegulatoryItemOut] = []
    history: list[HistoryDayOut] = []
    #: 价格未达线、但换手率口径单独达标的票（旁路，不与 items 重复）
    turnover_only: list[TurnoverOnlyOut] = []
    #: `daily_basic_indicators` 是否已更新到 `as_of`（false 时旁路为空，不等于无异动）
    turnover_available: bool = False
    turnover_as_of: date | None = None
    thresholds: list[ThresholdOut] = []
    caveats: list[str] = []
