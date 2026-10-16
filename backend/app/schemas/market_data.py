"""Market-data face response schemas."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field


class GlobalIndexCardOut(BaseModel):
    """全球指数卡片（S1.3）。

    **五要素必填**（名称 / 数据源代码 / 交易时段 / 时区 / 币种）——`session` / `timezone` /
    `currency` 不给默认值：登记表漏填时读端点必须**炸**（422），不允许静默给出空串让
    前端显示一片空白（那正是 S1.3「非空率 100%」要防的假绿）。
    """

    ts_code: str
    name: str
    market: str
    region: str
    session: str  # 交易时段（A50 = 新加坡 T+1 夜盘，不得与 A 股时段混淆）
    timezone: str  # IANA 时区名，如 America/New_York
    currency: str  # 计价币种
    price: float | None = None
    change: float | None = None
    pct_change: float | None = None
    spark: list[float] = Field(default_factory=list)
    updated_at: datetime
    #: 数据源代码/来源（登记表的 `source`：index_global / index_daily / sina_index）
    data_source: str
    #: 这一格价来自哪一路：realtime / realtime_sina / eod / none
    price_source: Literal["realtime", "realtime_sina", "eod", "none"]


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
    """板块资金流列表 envelope：`as_of` = **返回的 items** 自己持有的最近日
    （该 dimension 的 per-dimension max，各维度可能不同）；items 为空时 None。
    """

    as_of: str | None = None  # ISO 日期（返回 items 的日）；无 items 时 None
    stale_days: int | None = None  # 自然日差（今天 - as_of）；无 items 时 None
    items: list[SectorMoneyflowOut] = Field(default_factory=list)


# ── Task 6b：板块趋势（GET /market/sector-trends）───────────────────────────────
#
# 读时计算，**不物化**。三条硬约束（Ruling 21/22 + 计划缺日语义）：
# 1. 序列只吃"完整日"（`capture_kind='eod_full'`）；窗口内缺完整日的交易日 → `missing_days`，
#    **绝不**当成 0 流入；
# 2. `attribution` 必填且如实：`industry`/`region` = 按当前快照归属（UI 固定文案
#    "按当前分类回溯，非当时成分"）、`concept` = 时点成分（`concept_member_changes` 反推）；
# 3. `top_n` 是显式维度（默认 20）—— 榜单口径写进响应，不藏在代码里。

#: 归属口径（Ruling 22）。`industry`/`region` 无成分差分表 → 只能按当前分类回溯；
#: `concept` 有 `concept_member_changes`（2026-09-20 起）→ 时点成分。
Attribution = Literal["current_sw_snapshot", "point_in_time"]

#: 板块涨停家数序列的归属来源。`industry`/`region` 没有"东财板块 → 个股"的映射表
#: （`concept_members` 只覆盖 concept；`sw_industry_members` 是申万 L3 码，与东财 BK 码
#: 体系不同源），故只有 concept 能算 —— 其余维度显式 `null`，**不**用名字相近的申万码
#: 冒充东财板块（那会造出"看起来有"的错误归属）。
LuCountSource = Literal["point_in_time_membership", "unavailable"]


class SectorTrendPointOut(BaseModel):
    """板块在某个**完整日**的观测（缺日不出现在序列里）。"""

    trade_date: date
    main_net_inflow: float | None = None  # 元
    pct_change: float | None = None  # %
    up_count: int | None = None
    down_count: int | None = None
    # 板块涨停家数：只有 concept 维度能算（见 LuCountSource）；None = 不适用/缺数据
    lu_count: int | None = None


class SectorTrendBoardOut(BaseModel):
    """单板块在窗口内的连续性统计 + 逐日序列。

    四组连续性指标（`streak_*` = **截至窗口最后一天**的当前连击；`max_streak_*` =
    窗口内最长连击）都只在**完整日**上计算：缺日既不续接也不打断连击，因为"这天没有
    完整快照"根本不是一个观测（Ruling 21）。

    `series` 只含完整日（缺日不出现在这里，见 `missing_days`）；`main_net_inflow` 是
    **最后完整日**的值，为 None 表示该行上游缺值（**不是** 0 流入）。
    """

    board_code: str
    board_name: str | None = None
    main_net_inflow: float | None = None  # 元（最后完整日）
    pct_change: float | None = None  # %
    up_count: int | None = None
    down_count: int | None = None
    # 上榜口径：当日 `main_net_inflow` 降序前 `top_n`（tiebreak `board_code ASC`）
    streak_on_top_n: int = 0
    max_streak_on_top_n: int = 0
    streak_inflow: int = 0  # 截至末日的连续净流入天数
    max_streak_inflow: int = 0
    streak_outflow: int = 0
    max_streak_outflow: int = 0
    series: list[SectorTrendPointOut] = Field(default_factory=list)


class SectorTrendWindowOut(BaseModel):
    """窗口自描述。窗口 = **以该维度快照的最近日为右端、向前恰好 `days` 个交易日**。

    - `days` = **请求**的交易日数（原样回显，不拿观测值冒充）；
    - `observed_days` = 实际落在窗口内的交易日数（该维度数据起点附近 / `daily_quotes`
      更短时会 `< days`；`== 0` 只在"该维度一行都没有"时出现）；
    - `samples` = 其中**完整日**的数量（= 序列长度）；
    - `from`/`to` 是实际纳入的首/末**完整日**（全缺时均 None）。

    三者并排让"窗口天数不符请求值"**不可能悄悄发生**（Task 6 评审 Important #2）。
    不变量：`samples + len(missing_days)` ≤ `days`，且 ≤ `observed_days`。
    """

    days: int
    observed_days: int = 0
    from_: date | None = Field(default=None, alias="from")
    to: date | None = None
    samples: int = 0

    model_config = {"populate_by_name": True}


class LeaderChangeOut(BaseModel):
    """龙头更替事件：`trade_date` 当日榜首换人（首日不算更替）。

    每行的 `board_code`/`board_name` 是**当日**的新龙头（不是被替换掉的旧龙头）。
    """

    trade_date: date
    board_code: str
    board_name: str | None = None


class SectorTrendsOut(BaseModel):
    """``GET /market/sector-trends`` 响应。

    `available_from` 必填（可为 ``None`` = 该维度**还没有**任何完整日，如 T2 的 15:05
    EOD job 上线前；此时 `boards=[]`、窗口内每个交易日都在 `missing_days` —— 绝不拿
    `poll` 行冒充、绝不 0 流入）。`attribution` 必填且随 `dimension` 变化。

    该维度**一行都没有**（连 `poll` 行都没有）时是另一条路径：`available_from=null`、
    `window.observed_days=0`、`missing_days=[]`（"没有可统计对象" ≠ "有对象但缺完整日"，
    两者不能长成同一个形状）。
    """

    dimension: str
    as_of: date | None = None  # 最后完整日；无完整日时 None
    available_from: date | None = None  # 该维度最早完整日（Ruling 21 必填字段）
    attribution: Attribution
    attribution_note: str
    lu_count_source: LuCountSource = "unavailable"
    top_n: int = 20
    window: SectorTrendWindowOut
    boards: list[SectorTrendBoardOut] = Field(default_factory=list)
    #: 请求窗口内**缺完整日**的交易日（isoformat 升序）。缺日 ≠ 0 流入：它们不出现在
    #: 任何序列/连击里，只在这里被显式暴露。
    missing_days: list[str] = Field(default_factory=list)
    leader_change_count: int = 0
    leader_changes: list[LeaderChangeOut] = Field(default_factory=list)


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
    history_as_of: str | None = None  # 返回 history 末行的日 ISO；history 为空时 None
    history_stale_days: int | None = None  # 自然日差；history 为空时 None


class NorthboundPointOut(BaseModel):
    """北向单日点：``amount`` 的含义**由 ``amount_kind`` 决定**（Ruling 62/63）。

    口径**必填**（无默认值）：漏标口径时读端点必须 422，而不是静默让前端把成交额
    当成净流入。
    """

    date: str
    amount: float | None = None  # 万元；口径见 amount_kind
    amount_kind: Literal["net_buy", "turnover"]


class NorthboundSeriesOut(BaseModel):
    """北向 ``moneyflow_hsgt.north_money`` 日序列 envelope（Ruling 62/63）。

    ⚠️ **该列在 2024-08-19 换过语义**：``amount_kind="net_buy"``（断点前）的 ``amount``
    是**净买入**（可正可负）；``amount_kind="turnover"``（断点起）的 ``amount`` 是
    **成交额**（恒正）。故信封的 ``amount_kind`` 是**本窗口**的口径：同一口径 → 该值，
    跨断点混装 → ``"mixed"``，无 items → ``None``。

    **读方不得把 ``amount`` 当净流入**：求和只在同一口径内成立；``"mixed"`` 必须拒绝
    求和（把净买入与成交额相加是纯粹的错数）。上游停更由 ``source_status`` 显式标注。
    """

    as_of: str | None = None  # ISO 日期（返回 items 末项的日）；无 items 时 None
    stale_days: int | None = None  # 自然日差（今天 - as_of）；无 items 时 None
    source_status: Literal["live", "discontinued"] = "discontinued"
    # 本窗口口径（net_buy / turnover / 跨断点 mixed）；无 items → None
    amount_kind: Literal["net_buy", "turnover", "mixed"] | None = None
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


# ── 数据新鲜度巡检（GET /market/data-freshness）──────────────────────────────
# 字段与 reconciliation_service.reconcile_market_data(apply=False) 和
# coverage_report() 的返回一一对应；日期统一 isoformat 字符串（JSON 直出，前端零解析）


class DomainFreshnessOut(BaseModel):
    latest_in_db: str | None
    missing_days: list[str]
    partial_days: list[str]
    refetched: list[str]
    status: str  # ok | refetched | stale


class JobFailureOut(BaseModel):
    """最近失败的调度任务（来自 job_alert_service 的 `job:failures` 登记表）。"""

    job_id: str  # scheduler 注册 id（与 runner.py 的 add_job(id=...) 一致）
    # repr(exc) 截断到 500 字符（job_alert_service.MAX_ERROR_CHARS）；含异常类型，
    # 够定位不必翻日志（StatementError 的 repr 可能带换行，故不是严格单行）
    error: str
    at: str  # ISO8601（Asia/Shanghai）


class AdjFactorRepairOut(BaseModel):
    """reconcile 重拉 daily_quotes 后对 adj_factor 缺口的补洞结果。

    apply=False（只读巡检）不补洞，字段为 null。``failed`` = 外呼/DB 失败的股票数；
    ``unfilled`` = 处理后仍有缺口未填的股票数（TuShare 无该日因子）；``remaining`` =
    被预算截断、本次未处理的候选股票数。``error`` = 整体失败原因（``类型: 消息``），
    非致命；结果随 worker 回执与 scheduler 日志外露——``/market/data-freshness``
    恒走 apply=False，本对象在那里恒为 null，不经该端点展示。
    """

    days: list[str]  # 本次重拉并检查的交易日（isoformat）
    rows: int  # 实际填入的缺口行数（UPDATE-only，不含同值重写）
    failed: int  # 外呼/DB 失败的股票数（0 = 全部成功）
    unfilled: int = 0  # 处理后仍有缺口未填的股票数（0 = 全部填上）
    remaining: int = 0  # 因预算截断未处理的候选股票数（0 = 本轮未截断）
    error: str | None = None  # 整体异常（类型 + 消息），无异常为 null


class CoverageOut(BaseModel):
    """单域覆盖率（Task 3 只读巡检；`reconciliation_service.coverage_report` 的一行）。

    两个数**必须并排**（Ruling 13）：``have_days`` = 有任何行的交易日数（与计划
    Why-now 表对拍的形状）；``complete_days`` = "完整日"（``sector_moneyflow`` 只认
    ``capture_kind='eod_full'`` 的行，盘中 ``poll`` 行是 inflow-Top-100 子集）。其余域
    ``complete_days == have_days``。``cover_pct`` 基于 ``complete_days``。

    缺失渲染 ``--`` / ``null``，**绝不 0**：``cover_pct=None`` 表示"分母为空"（还没有
    交易日数据），不是"0% 覆盖"。``status != "ok"``（``missing_table`` /
    ``missing_column``）表示该域压根没查成（迁移未应用），此时所有计数为 0。
    """

    domain: str  # 巡检域名（与 reconcile 的可自愈域**不重名**）
    table: str  # 实际查询的表（便于运维定位）
    date_column: str  # 业务日期列（交易日窗口按它计算）
    row_count: int = 0  # 窗口内行数（非全表行数）
    have_days: int = 0  # 有任何行的交易日数
    complete_days: int = 0  # 完整日的交易日数（其余域 == have_days）
    trading_days: int = 0  # 分母：该域区间内的**交易日**数（见 TRADING_DAY_SOURCES 并集）
    cover_pct: float | None = None  # complete_days / trading_days；分母为 0 → None
    missing_days: list[str] = []  # 窗口内缺行的交易日（isoformat，升序）
    # 窗口外/非交易日的行，**不是**"未来脏行"的同义词：当日盘前/盘中写下的合法行
    # （sector_moneyflow 的 poll 行、sentiment_intraday 的当日行）只要落在窗口外就会
    # 出现在这里。判据是"日期不在窗口内"，不是"这行是脏的"；不参与覆盖率计分。
    extra_days: list[str] = []
    first_day: str | None = None
    last_day: str | None = None
    status: str = "ok"  # ok | missing_table | missing_column
    # 分母口径降级：**并集提供了主源 `daily_quotes` 缺失的交易日**（`union_days !=
    # primary_days`），或主源表不可用（未建/迁移未应用）。换分母必须可见，不能静默换。
    # 只判"表是否存在"会漏掉真实状态：主源在库但缺日、由 `stock_price_limits` 补上时，
    # 分母口径已经变了却标 ok（评审 Important #1）。名字与 reconcile 的
    # `degraded_calendar`（trade_cal 外呼降级）**故意区分**：一个是"本地日历源换了"，
    # 一个是"tushare 日历降级成工作日启发式"。
    degraded_trading_calendar: bool = False


class MarketMetricsLegOut(BaseModel):
    """派生指标（§S1.7–§S1.13）**一条腿**的落库点与落后交易日数（只读排障面）。"""

    key: str
    label: str
    latest: str | None
    #: `latest` 之后到最近交易日之间的交易日数；`latest` 缺失 ⇒ `None`（不是 0）。
    lag_trading_days: int | None
    #: 该腿是否喂综合指数（§S1.13）：非成分腿落后**不**阻断 composite。
    feeds_composite: bool


class MarketMetricsFreshnessOut(BaseModel):
    """四条腿的收敛状态（这一族此前**只**由手工脚本写 —— 2026-10-16 事故的暴露面）."""

    target: str | None
    checked_at: str | None
    legs: list[MarketMetricsLegOut] = []


class DataFreshnessOut(BaseModel):
    as_of: str
    expected_latest: str | None
    expected_days: int
    universe: int
    # 最新期望日 daily_quotes 的实际行数（= 有行情的股票数）。与 universe 并排看即可
    # 发现名录滞后——universe 取自 stocks 表，该表冻结时 threshold 分母本身就偏小。
    quotes_symbols_latest: int | None
    degraded_calendar: bool
    apply: bool
    domains: dict[str, DomainFreshnessOut]
    # 对账补灌会新插入因子为 NULL 的行（TuShare daily 不带因子）；该键记录每次
    # apply 对账顺带补了多少缺口、是否失败/截断。freshness 端点复用 apply=False
    # 只读路径，本字段恒为 null（结果在 reconcile 返回值 / worker 回执与日志里）。
    adj_factor_repaired: AdjFactorRepairOut | None = None
    # 失败任务登记：APScheduler 的 "Job executed successfully" 只说明函数返回了，
    # 函数内被 except 吞掉的失败只有这里看得见（Redis 不可用时为空列表）。
    failed_jobs: list[JobFailureOut] = []
    # Task 3：只读巡检域覆盖率（加字段；老 producer 不传 → 空列表，端点照常 200）。
    # 这些域**没有** `_refetch_*`，永远不会出现在 `domains[*].refetched` 里。
    coverage: list[CoverageOut] = []
    # 派生指标（§S1.7–§S1.13）四条腿的收敛状态（加字段；老 producer 不传 → null）。
    # 这一族此前只由手工回补脚本写，**没有任何定时任务**碰过它 —— 2026-10-16 的
    # "数据停在 10月12日"就是这个暴露面。这里让"哪条腿停在哪天"在巡检端点直接可读。
    market_metrics: MarketMetricsFreshnessOut | None = None
