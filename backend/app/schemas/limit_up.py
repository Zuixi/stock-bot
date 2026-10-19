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
    # 2026-10-18 追加：今开溢价与昨日涨停样本数。老消费方忽略即可（**追加**，不改既有字段）。
    yzt_avg_open_premium: float | None = None
    yzt_n: int = 0
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
    yzt_avg_pct: float | None = None
    yzt_avg_open_premium: float | None = None
    yzt_n: int = 0
    promo_1to2: float | None = None
    promo_1to2_n: int = 0
    promo_2to3: float | None = None
    promo_2to3_n: int = 0
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
    # 三态：True 值会被重算改写（缺 T+1 披露的成分且仍在收敛窗口内）/ False 终值 /
    # None 判不出（缺参与集登记或缺交易日历）——None **不得**被读成"终值"。
    # 2026-10-17 换锚：expected_as_of 由交易日历 + 时钟判定（交易日 17:30 后即当日），
    # lag_trading_days 由**日历计数**（旧实现两者都来自 `max(daily_quotes)`，
    # 与滞后的数据链同源 ⇒ "今天整天没采"时反而报 stale=false）。
    provisional: bool | None = None


# ── 情绪指标详情（GET /market/sentiment/metrics/{metric_key}）──────────────────────
#
# 读时计算、**不物化**：数据全部来自 `market_sentiment_daily`（已按交易日冻结）。
# 与 `/sentiment/trends` 的分工：那个给"三个指标的分位 + 全量序列"；本端点给"任意一个
# 指标自己的历史位置"（统计量 / 分布 / 连续天数 / 样本量），供点击卡片后的详情页消费。
#
# 三条纪律与趋势端点一致：
# 1. **NULL 不进样本**（分母为 0 的比值日既不进样本也不计 0）；
# 2. 有效样本 < `MIN_PERCENTILE_SAMPLES` ⇒ `percentile_rank=None` + `insufficient_history=true`；
# 3. 不插值、不补缺日 —— `series` 里缺的日子就是没有这一天。

#: 指标详情页支持的指标（与 `SentimentThermometer` 的 9 张卡 1:1）。
#: 加指标必须同时改 `sentiment_metric_service.METRIC_DEFS` 与前端 `SentimentMetricKey`。
SENTIMENT_METRIC_KEYS = (
    "zt_count",
    "zb_count",
    "dt_count",
    "broken_rate",
    "yzt_avg_pct",
    "yzt_avg_open_premium",
    "promo_1to2",
    "promo_2to3",
    "max_streak",
)

#: 指标的展示量纲：计数（家）/ 板 / 比值（0–1，前端 ×100）/ 百分数（已是 %）。
SentimentMetricUnit = Literal["count", "board", "ratio", "pct"]

#: 指标所属的情绪模块（与温度计的分组文案一致）。
SentimentMetricGroup = Literal["breadth", "money", "promotion", "space"]


class SentimentMetricMetaOut(BaseModel):
    """指标自描述：标签、量纲、口径与"高代表更热还是更冷"。"""

    key: str
    label: str
    unit: SentimentMetricUnit
    group: SentimentMetricGroup
    #: 高值代表情绪更热（`high`）还是更冷（`low`）——决定前端配色与「越高越热」提示，
    #: 不在前端按 key 硬编码（同一份口径只写一处）。
    hotter_when: Literal["high", "low"]
    #: 已裁定口径原文（删减自 docs/design/limit-up-sentiment.md §3）
    caliber: str


class SentimentMetricPointOut(BaseModel):
    """一个交易日的指标值。`value=None` = 当日算不出（分母 0/缺行情），**不是 0**。"""

    trade_date: date
    value: float | None = None
    #: 当日样本数（`yzt_n` / `promo_*_n` / 炸板率的 `zt+zb`）；非样本型指标恒 `null`。
    n: int | None = None
    #: 样本不足（n < `MIN_SAMPLE_N`）：值仍展示，但不进统计量/分位/分布。
    low_sample: bool = False


class SentimentMetricStatsOut(BaseModel):
    """窗口内**有效样本**的统计量（`sample_count` 是有效样本数，不是窗口行数）。"""

    sample_count: int
    min: float | None = None
    max: float | None = None
    mean: float | None = None
    median: float | None = None
    p10: float | None = None
    p25: float | None = None
    p75: float | None = None
    p90: float | None = None
    #: 总体标准差（不是样本标准差）：只用来描述窗口波动，不做统计推断。
    std: float | None = None


class SentimentMetricBinOut(BaseModel):
    """直方图的一个等宽区间 `[from, to)`（最后一箱含右端点）。"""

    from_: float = Field(alias="from")
    to: float
    count: int

    model_config = {"populate_by_name": True}


class SentimentMetricStreakOut(BaseModel):
    """截至最新有效日的连续同向天数（以窗口中位数为界）。

    `side=None` 表示最新一日**恰好等于**中位数或当日无值 —— 此时不给"连续 N 天"的断言。
    """

    side: Literal["above", "below"] | None = None
    days: int = 0
    median: float | None = None


class SentimentMetricPeerOut(BaseModel):
    """同页指标切换器的一项：标签 + 最新原始值（与当前指标的读窗口同源）。

    `low_sample=true` 表示这个最新值 n<5：值照常展示，但调用方应加"样本小"标记，
    不能拿它和"200 个样本的点估计"并排读。
    """

    key: str
    label: str
    unit: SentimentMetricUnit
    latest_value: float | None = None
    latest_date: date | None = None
    low_sample: bool = False


# ── 情绪脉搏（GET /market/sentiment/pulse）：small multiples + 阶段 + 指数对比 ─────
#
# 一次响应同时给：9 个指标的逐日序列、周期阶段区段、基准指数 5 日收益分位、
# 情绪 vs 指数的背离段。前端「情绪脉搏」区块只渲染，不再自行统计。


class SentimentRegimeInputOut(BaseModel):
    key: str
    label: str
    value: float | None = None
    percentile: float | None = None
    unit: SentimentMetricUnit


class SentimentRegimeCurrentOut(BaseModel):
    stage: str
    label: str
    tone: Literal["cold", "neutral", "hot", "extreme", "muted"]
    since: date
    days: int
    inputs: list[SentimentRegimeInputOut] = []


class SentimentRegimeSegmentOut(BaseModel):
    stage: str
    label: str
    tone: Literal["cold", "neutral", "hot", "extreme", "muted"]
    from_: date = Field(alias="from")
    to: date
    days: int

    model_config = {"populate_by_name": True}


class SentimentRegimeOut(BaseModel):
    version: str
    current: SentimentRegimeCurrentOut | None = None
    segments: list[SentimentRegimeSegmentOut] = []


class SentimentPulseSeriesPointOut(BaseModel):
    """情绪脉搏的逐日序列（9 个指标；与 `/sentiment/trends` 同源投影）。"""

    trade_date: date
    zt_count: int
    dt_count: int
    zb_count: int
    broken_rate: float | None = None
    yzt_avg_pct: float | None = None
    yzt_avg_open_premium: float | None = None
    yzt_n: int = 0
    promo_1to2: float | None = None
    promo_1to2_n: int = 0
    promo_2to3: float | None = None
    promo_2to3_n: int = 0
    max_streak: int = 0


class SentimentBenchmarkPointOut(BaseModel):
    trade_date: date
    close: float | None = None
    #: 指数 5 个交易日收益（%）；指数自身行数不足 6 时 null
    pct_5d: float | None = None


class SentimentBenchmarkOut(BaseModel):
    ts_code: str
    name: str
    points: list[SentimentBenchmarkPointOut] = []


class SentimentComparisonPointOut(BaseModel):
    """情绪与指数的**同分位轴**对比（都按展示窗口重算，`<=` 口径）。"""

    trade_date: date
    sentiment_pct: float | None = None
    index_pct: float | None = None
    index_pct_5d: float | None = None


class SentimentDivergenceOut(BaseModel):
    from_: date = Field(alias="from")
    to: date
    days: int
    kind: Literal["sentiment_stronger", "index_stronger"]
    label: str


class SentimentPulseOut(BaseModel):
    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    window: SentimentWindowOut
    series: list[SentimentPulseSeriesPointOut] = []
    regime: SentimentRegimeOut
    benchmark: SentimentBenchmarkOut
    comparison: list[SentimentComparisonPointOut] = []
    divergence: list[SentimentDivergenceOut] = []
    current_divergence: SentimentDivergenceOut | None = None


# ── 指标详情的样本门槛 / Wilson 区间 ──────────────────────────────────────────
#
# 小样本纪律（2026-10-18）：样本型指标（晋级率、昨日涨停均值、炸板率的分母）
# 有效样本 < `MIN_SAMPLE_N` 的日子**不进**统计量/分位/分布；比值类的最新值额外给
# 95% Wilson 区间，避免把 `n=2 的 50%` 当点估计读。


class SentimentMetricIntervalOut(BaseModel):
    low: float
    high: float
    level: float = 0.95
    n: int
    method: Literal["wilson"] = "wilson"


class SentimentMetricSampleGateOut(BaseModel):
    field: str | None = None
    min_n: int
    excluded_days: int


class SentimentMetricDetailOut(BaseModel):
    """``GET /market/sentiment/metrics/{metric_key}`` 响应。"""

    metric: SentimentMetricMetaOut
    as_of: date | None = None
    as_of_quality: AsOfQuality = "partial"
    window: SentimentWindowOut
    latest: SentimentMetricPointOut | None = None
    previous: SentimentMetricPointOut | None = None
    #: 最新有效值 − 上一行有效值（量纲同指标：比值差 = 百分点 ×100 前的原值）
    delta: float | None = None
    #: 最新值在窗口有效样本里的分位（0–100，`<=` 口径，四舍五入 2 位）；样本不足为 `None`
    percentile_rank: float | None = None
    stats: SentimentMetricStatsOut | None = None
    distribution: list[SentimentMetricBinOut] = []
    streak: SentimentMetricStreakOut = SentimentMetricStreakOut()
    series: list[SentimentMetricPointOut] = []
    #: 最新值的 95% Wilson 区间（仅比值类且有足够样本时给；否则 null）
    latest_interval: SentimentMetricIntervalOut | None = None
    #: 样本门槛披露：`min_n` 与因样本不足被排除的天数
    sample_gate: SentimentMetricSampleGateOut | None = None
    #: 指标所在的情绪周期阶段（与 pulse 同一套规则；供详情图背景带）
    regime: SentimentRegimeOut | None = None
    #: 切换器：9 个指标的最新有效值（同一次读窗口，不额外查库）
    peers: list[SentimentMetricPeerOut] = []
    insufficient_history: bool = True
