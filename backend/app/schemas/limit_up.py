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

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
    # 盘中路径：东财 hybk 体系不映射 SW，l3_*/l1_* 全 None —— 此时**板块名走 `board_name`**
    # （东财板块名），前端据此把卡片口径切成「盘中板块（东财口径）」。
    # 不再依赖前端“自己判断 null”，必须显式给出可渲染名（否则卡片只能显示 --）。
    l3_code: str | None = None
    l3_name: str | None = None
    l1_code: str | None = None
    l1_name: str | None = None
    board_name: str | None = None
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


class SentimentIntradayPointOut(BaseModel):
    """盘中分时点（Task 12 ``GET /market/sentiment/intraday`` 单行 schema）。

    与 ``market_sentiment_intraday`` 表列 1:1 对齐；端点按 ``captured_at`` 升序返回
    若干点，前端直接以 ``captured_at`` 为 X 轴渲染时序。
    """

    id: int
    trade_date: date
    captured_at: datetime
    zt_count: int
    dt_count: int
    zb_count: int
    max_streak: int


# ── Task 6a：情绪趋势 + 历史分位（GET /market/sentiment/trends）────────────────────
#
# 读时计算，**不物化**：SQL over `market_sentiment_daily`（该表本身是 T4 回补后的
# 派生缓存），分位在服务层按**有效样本**算。响应形状与 brief 6a 逐字对齐；
# `percentile` 里只有 3 个指标（拍板 R6），字段口径见 `SentimentPercentileOut`。

#: 允许做历史分位的指标（拍板 R6：只 3 个，不得扩张）。
#: `zt_count` 每日必有效；`broken_rate`/`promo_1to2` 在分母为 0 的日子为 NULL
#: —— 那些日子**不进**该字段样本（NULL ≠ 0）。
PERCENTILE_FIELDS = ("zt_count", "broken_rate", "promo_1to2")

#: 分位不足的历史门槛（Ruling 4）：有效样本 < 60 → `insufficient_history=true` 且
#: `percentile=null`（不给假分位）。
MIN_PERCENTILE_SAMPLES = 60


class SentimentTrendPointOut(BaseModel):
    """窗口内一个交易日的情绪行（`market_sentiment_daily` 的投影）。

    缺失渲染 ``null``（前端 ``--``），**绝不** 0：`broken_rate`/`promo_1to2` 为 NULL
    表示"当日算不出该比值"（分母 0），不是"比值是 0"。
    """

    trade_date: date
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    promo_1to2: float | None = None
    max_streak: int = 0


class SentimentWindowOut(BaseModel):
    """窗口自描述：`days` 是**请求**的交易日数；`samples` 是实际纳入的行数。

    `samples < days` 时缺口由"该表自己持有的交易日"决定（T4 之前只有 21 天）；
    `from`/`to` 是**实际纳入**的首/末日（无行时均为 None，不拿"今天"冒充）。
    """

    days: int
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    samples: int = 0

    model_config = {"populate_by_name": True}


class SentimentPercentileOut(BaseModel):
    """单指标的历史分位（nearest-rank，样本升序第 ``ceil(pct/100 × n)`` 个）。

    只有**有效样本**参与：字段为 NULL 的日子被排除（绝不按 0 计）。`p0`/`p100`
    分别等于样本的最小/最大值 —— 端点值可达且正确（Ruling 4）。
    `sample_count` 是**该字段**的样本数（三个指标各不相同），不是窗口行数。
    """

    field: str
    sample_count: int
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    p0: float | None = None
    p10: float | None = None
    p25: float | None = None
    p50: float | None = None
    p75: float | None = None
    p90: float | None = None
    p100: float | None = None

    model_config = {"populate_by_name": True}


class SentimentStreakOut(BaseModel):
    """窗口内的连击统计。`zt_above_median_days` = `zt_count` **严格大于**窗口中位数的天数。"""

    zt_above_median_days: int = 0


class SentimentTrendsOut(BaseModel):
    """``GET /market/sentiment/trends`` 响应。

    `percentile` 为 ``None`` 即"样本不足，不给分位"（与 `insufficient_history` 同真值）；
    空窗口同样 ``None``。`as_of_quality` 复用 ``market_day_service`` 的完整性判据。
    """

    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    window: SentimentWindowOut
    series: list[SentimentTrendPointOut] = []
    percentile: dict[str, SentimentPercentileOut] | None = None
    streak: SentimentStreakOut = SentimentStreakOut()
    insufficient_history: bool = True


# ── S1.13 hero：综合指数的"今天 + 历史分位"（GET /market/sentiment/composite）───────
#
# 分位**必须由后端算**（Ruling 4 / `MIN_PERCENTILE_SAMPLES`）：前端只渲染，不做统计。


class SentimentCompositeOut(BaseModel):
    """``GET /market/sentiment/composite`` 响应（S1.13 市场情绪综合指数的读面 hero）。

    - `as_of`/`value`/`caliber_version` = **最新一个过发布闸门**的行
      （`effective_weight_sum >= COMPOSITE_PUBLISH_MIN_WEIGHT` = 0.80；`value` 可为
      ``None``：当日参与腿不足 / 生效权重不足时 S1.13 落 NULL，**不落 0**）；
      薄行（如 2026-10-12 的 3/11 条腿、0.23）**不作头条** —— 读面会退回上一个齐备日，
      并由 `lag_trading_days` 如实报出落后多少；
    - `window` = 分位窗口（交易日，冻结 1260）；`sample_count` = 窗口内**有效样本**数
      （值为 NULL 的行**不进**样本，也不计 0）；
    - `percentile` = 选中行的值在**截至该行**的窗口样本里的 nearest-rank 分位
      （0–100，**无插值**，四舍五入 2 位）；`insufficient_history=true` ⇒
      ``percentile=null``（有效样本 < 60 —— 与 `/sentiment/trends` 同一条纪律，
      **不给假分位**）；
    - `legs`/`leg_count`/`total_legs`/`effective_weight`/`min_effective_weight` 直读**选中
      行**的 `extra`（S1.13 写入的 `participants` / `effective_weight_sum` /
      `min_effective_weight` / `n_constituents`）；老行缺键 ⇒ ``legs=null``、计数 0、
      权重 ``null``（**不发明**旧口径）；
    - `expected_as_of` = 本地判定的"现在应该有的最新完整交易日"（`daily_quotes` 完整性
      判据，不外呼）；`lag_trading_days` = 选中行到它之间的**交易日**数；
      `stale` = 三态：``True`` 落后 / ``False`` 追平 / ``None`` **无法判定**
      （缺 `expected_as_of`）—— ``None`` 不得被读成"新鲜"。
    """

    as_of: date | None = None
    value: float | None = None
    window: int
    sample_count: int = 0
    percentile: float | None = None
    insufficient_history: bool = True
    legs: list[str] | None = None
    leg_count: int = 0
    total_legs: int = 0
    effective_weight: float | None = None
    min_effective_weight: float | None = None
    caliber_version: str | None = None
    expected_as_of: date | None = None
    lag_trading_days: int | None = None
    stale: bool | None = None
