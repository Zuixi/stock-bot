"""A股 7 榜的响应契约（S1.6）。

与既有的 :mod:`app.schemas.ranking`（5 个单日榜）**并存、互不影响**：那 5 个榜是
``daily_quotes`` 单列排序，形状装不下"财务来源 + 窗口聚合"，故 7 榜另开一套契约。

契约的三条硬性要求（spec §S1.6）由本模块的字段**承载**，而不是靠前端文案：

1. **口径必须可见**——:class:`RankingCaliberOut` 的三个字段即「以哪个交易日为准 /
   用的是哪期财务数据 / 是否含次新股 ST」，每榜一份，随 payload 下发。
2. **不足 100 条必须说明原因**——:attr:`RankingBoardResponseOut.notes` 是**结构化**
   的说明列表（不是可选文案），条数不足时至少有一条。
3. **财务类榜必须 PIT 正确**——:class:`FinancialReportRefOut` 把"实际用的是哪一期 +
   按哪个公告日判定可见性"写进 payload，逐行可核对（详见
   ``docs/decisions/0011-point-in-time-data-contract.md``）。
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.market import AsOfQuality

#: 7 个榜的 key。**ASCII**：它要进 URL 路径段（``/market/rankings/:key``），
#: 中文 key 会引入百分号编码与路由匹配的额外变量。
RankingBoardKey = Literal[
    "market_cap",
    "dividend_yield",
    "monthly_gain",
    "monthly_loss",
    "half_year_profit",
    "annual_profit",
    "roe",
]

#: 用户可见的分组（UI 用它分 Tab 组；口径权威在 docs/design/metric-dictionary.md）。
RankingBoardCategory = Literal["valuation", "price", "financial"]

#: 榜单的数据来源家族——决定"用的是哪期财务数据"这一栏是否有意义。
RankingBoardSource = Literal["daily_basic", "daily_quotes_window", "financial_statement"]


class FinancialReportRefOut(BaseModel):
    """财务类榜逐行携带的"我是按哪一期排进来的"。

    为什么逐行而不是整榜一个：PIT 取"该交易日**已公告**的最新一期"，不同公司公告
    节奏不同 → 同一榜上可能同时出现 2026-06-30（中报）与 2026-03-31（一季报）。
    整榜写一个"最新期"就是把这句话说错。
    """

    model_config = ConfigDict(from_attributes=True)

    end_date: date
    """报告期（``financial_report_versions.end_date``）。"""

    report_type: str
    """报告期类型：``FY`` / ``H1`` / ``Q1`` / ``Q3``（由 end_date 月份推出）。"""

    ann_date: date
    """**实际用于 PIT 判据**的公告日 = ``coalesce(f_ann_date, ann_date)``。

    为什么取这个而不是单取 ``ann_date``：``f_ann_date`` 是"实际公告日"（该公司实际
    披露该期数据的日期），``ann_date`` 是"公告日期"。取 ``coalesce(f_ann_date,
    ann_date)`` = 有实际公告日就用实际公告日，缺失时才退回公告日期——两种来源都不会
    把报告期早于真实披露时间的股票算进来（保守方向），也就不会前视。
    """

    ann_date_source: Literal["f_ann_date", "ann_date"]
    """上值的来源字段。判别式而非静默回退（ADR 0011；S1.2 Ruling 34 的先例）：
    消费者看得出这一行是用实际公告日还是用公告日期判的。"""


class RankingBoardItemOut(BaseModel):
    """一行。各榜只填自己那几个字段，其余为 ``None``（一套 schema 覆盖 7 榜）。"""

    model_config = ConfigDict(from_attributes=True)

    rank: int
    symbol: str
    name: str
    exchange: str | None = None
    close: float | None = None
    pct_chg: float | None = None

    total_mv: float | None = None  # 万元（TuShare daily_basic 原单位）
    dv_ratio: float | None = None  # %
    monthly_pct_chg: float | None = None  # %
    n_income: float | None = None  # 元
    n_income_attr_p: float | None = None  # 元
    roe: float | None = None  # %

    is_st: bool = False
    """名称含 ``ST``（含 ``*ST``）。与限价判据无关，只作口径披露的机械计数用；
    含 ``ST`` 的判定见 :mod:`app.services.ranking_board_service` 的列注释。"""

    is_new_stock: bool = False
    """次新股 = ``list_date`` 距口径交易日不足 365 天（日历日）。"""

    financial_report: FinancialReportRefOut | None = None
    """财务类榜才有；行情类榜为 ``None``（它们不消费财务数据）。"""


class RankingCaliberOut(BaseModel):
    """每榜必备的三件事（spec §S1.6）。"""

    trade_date: date
    """① 以哪个交易日为准（= 响应 ``as_of``，独立字段是给 UI 直读，不必猜）。"""

    financial_period: str
    """② 用的是哪期财务数据。

    行情类榜无财务数据 → 显式写 ``"不适用（该榜不使用财务数据）"``，
    **不留空**（空字符串会被 UI 渲染成"没写口径"）。
    """

    sample_scope: str
    """③ 是否含次新股/ST——写**实际执行**的取值，不是默认值。"""


class RankingBoardResponseOut(BaseModel):
    as_of: date
    as_of_quality: AsOfQuality = "complete"
    as_of_reason: str | None = None
    is_latest_trading_day: bool

    key: RankingBoardKey
    label: str
    category: RankingBoardCategory
    source: RankingBoardSource
    sort_direction: Literal["DESC", "ASC"]

    metric_unit: str
    """排序指标的原始单位（**原样透传**，不做换算）。"""

    limit: int
    returned_rows: int
    eligible_rows: int
    """该榜在口径下**可参与排序**的标的总数（分母）。

    ``< limit`` 时返回条数必然更少，:attr:`notes` 会说明原因。
    """

    truncated_ties: bool = False
    """截断边界是否有并列：第 ``limit + 1`` 名的指标值 == 第 ``limit`` 名。

    为真时，榜尾是"按 ``stock_id`` 升序"切出来的，**不是**唯一的前 N 名——这是
    "为什么看起来少/边界可疑"的诚实披露，而不是静默。
    """

    notes: list[str] = Field(default_factory=list)
    """不足 100 条 / 边界并列 / 无已公告财务数据 等情形的**结构化**说明。"""

    caliber: RankingCaliberOut
    items: list[RankingBoardItemOut]
