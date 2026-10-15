"""S1.4 · 申万行业三级下钻的响应契约（7 项行情字段 + SCD2 成分归属）。

**7 项字段**（spec §S1.4「指标字段 7 项」）在本文件里的落点::

    K 线（开/收/高/低）  → KlineMedianOut（行业层）/ open/close/high/low（个股层）
    涨跌幅               → pct_chg_weighted（成交额加权）/ pct_chg（个股层）
    换手率               → turnover_rate / turnover_rate_weighted（成交额加权，%）
    成交量               → volume（股）
    量比                 → volume_ratio / volume_ratio_weighted（成交量加权）
    振幅                 → amplitude（%）
    成交额               → amount（**元**）

**单位纪律**（踩过的坑见 docs/references/best-practices/database-performance.md）：

* ``daily_quotes.amount`` 存的是 TuShare 原生**千元** → 本层统一 ×1000 输出**元**
  （与 ``StockEnrichedOut.amount`` 的"原值透传"不同：那是既有契约，不改）。
* 行业层 K 线**不合成价格指数**：申万成品指数不在本任务的数据源内，凭成分股平均
  价格冒充"行业点位"会造出一条没有出处的伪序列。故只给**成分股 OHLC 的中位数**
  （如实标注为 median），并给出开→收的成交额加权涨跌幅作为行业涨跌幅。

**缺失不填 0**：任一字段无数据一律 ``None``，并附带 ``*_covered`` 覆盖数；
前端渲染 ``--``，不得用 0 冒充（Ruling 47：撤除 check-no-fake-zero 后由类型 + 本契约守住）。
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.schemas.market import AsOfQuality


class KlineMedianOut(BaseModel):
    """行业层 K 线：成分股 OHLC 的**中位数**（不是指数点位）。"""

    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    # 参与中位数的家数 = **当日真有行情行**的家数（= MarketIndustryQuoteOut.quoted_count）。
    # 名字不叫 member_count：它不是成分总数（那在 MarketIndustryQuoteOut.member_count），
    # 两个数在停牌/未采集时不同（Fix round 1 · 评审 Minor 指出原先文档与取值不一致）。
    quoted_count: int = 0


class Scd2CoverageOut(BaseModel):
    """最近一次 SCD2 成分 ingest 的覆盖摘要（**缺数对账的机械外露**，Fix round 1 · 评审 I5）。

    为什么放在读面而不是只写日志：spec §S1.4 要求"缺数有对账告警"。可核查的最小形态是——
    ingest 时算下界（每个指数要有行、单层行数不得塌方、0 行响应不得过多），
    把结论写进 ``sw_member_ingest_runs``，读面把**最近一次运行**原样外露。
    任何一个信号异常 → ``last_run_status != 'ok'`` 且 ``fully_ingested=false``，
    前端在 ``as_of`` 条上显示红标，而不是让缺数静默留在数据里。
    """

    last_run_as_of: date | None = None
    last_run_status: str | None = None
    fully_ingested: bool = False
    index_codes: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    zero_row_indices: int = 0
    detail: str | None = None

    @property
    def healthy(self) -> bool:
        return self.last_run_status == "ok" and self.fully_ingested


class MarketIndustryQuoteOut(BaseModel):
    """一个申万行业节点在某交易日的 7 项行情字段聚合。

    每个 ``*_covered`` 都是"该字段在成分股中**非空**的家数"，分母 = ``member_count``；
    ``quoted_count`` = 该日真有行情行（``daily_quotes`` 有行且 ``pct_chg`` 非空）的家数。
    ``member_count`` 可以大于 ``quoted_count``（停牌/未上市/未采集）；比值即"缺数"。
    """

    code: str
    name: str
    level: int
    parent_code: str | None = None

    # 成分与行情覆盖
    member_count: int
    quoted_count: int
    up_count: int
    down_count: int

    # ── 7 项字段 ────────────────────────────────────────────────────────
    # 1. K 线（成分股中位数，见 KlineMedianOut 的"不是指数点位"声明）
    # 成员来源：`level` = 节点自身层级的 SCD2 成员；`rollup` = 退回到"其下 L3 的并集"估算
    member_source: Literal["level", "rollup"] = "level"
    member_source_note: str | None = None

    kline_median: KlineMedianOut
    # 2. 涨跌幅（成交额加权，%）
    pct_chg_weighted: float | None = None
    pct_chg_covered: int = 0
    # 3. 换手率（成交额加权，%）
    turnover_rate_weighted: float | None = None
    turnover_rate_covered: int = 0
    # 4. 成交量（股，求和）
    volume: float | None = None
    volume_covered: int = 0
    # 5. 量比（成交量加权）
    volume_ratio_weighted: float | None = None
    volume_ratio_covered: int = 0
    # 6. 振幅（%）：成分股振幅的均值（每只 = (high-low)/pre_close*100）
    amplitude: float | None = None
    amplitude_covered: int = 0
    # 7. 成交额（**元**，求和；源为千元已 ×1000）
    amount: float | None = None
    amount_covered: int = 0


class MarketIndustryLevelItemOut(BaseModel):
    """下钻列表里的一行子行业：节点信息 + 该节点自身的 7 项聚合。"""

    code: str
    name: str
    level: int
    stock_count: int
    metrics: MarketIndustryQuoteOut | None = None


class MarketIndustryLevelOut(BaseModel):
    """某一层的下钻响应（L1 列表 / 某 L1 的 L2 列表 / 某 L2 的 L3 列表）。

    ``as_of`` = 行情口径日（``daily_quotes`` 的完整交易日，T+1 口径）；
    ``scd2_as_of`` = 分类归属口径日 = **最近一次成功 ingest 的日期**。
    两者**不是同一天**是常态（分类 ingest 每日一次、行情每日入库），前端必须分开展示
    ——把 ``scd2_as_of`` 藏起来就会让"成分缺失"被误读成"当天没有这只股票"。
    """

    as_of: date | None = None
    # ⚠️ 已声明的限度（Task 8 re-review，行为**有意不改**）：`as_of_quality` /
    # `as_of_reason` 总是描述**最新**完整交易日的行情覆盖（`_resolve_day` 不带 as_of），
    # 即使本响应按**历史** `as_of` 出数——历史上的那天行情是否齐全并不由此字段回答。
    # 成分口径另有 `scd2_as_of` / `scd2_coverage` 外露，不受此限度影响。
    as_of_quality: AsOfQuality = "partial"
    as_of_reason: str | None = None
    scd2_as_of: date | None = None
    scd2_available: bool = False
    scd2_note: str | None = None
    scd2_coverage: Scd2CoverageOut = Scd2CoverageOut()
    level: int
    parent: MarketIndustryLevelItemOut | None = None
    items: list[MarketIndustryLevelItemOut]


class MarketIndustryMemberOut(BaseModel):
    """末级成分股列表的一行：7 项字段 + SCD2 归属区间。"""

    symbol: str
    stock_code: str
    name: str | None = None

    # SCD2 归属（本行所属区间）
    effective_from: date
    effective_to: date | None = None

    quoted: bool = False
    # ── 7 项字段（个股层，原值口径）────────────────────────────────────
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    pct_chg: float | None = None
    turnover_rate: float | None = None
    volume: float | None = None
    volume_ratio: float | None = None
    amplitude: float | None = None
    amount: float | None = None  # 元


class MarketIndustryMembersOut(BaseModel):
    """末级（L3）成分股列表 + 该节点自身的 7 项聚合。"""

    as_of: date | None = None
    # 同 MarketIndustryLevelOut.as_of_quality 的已声明限度：历史 as_of 也带最新日的质量。
    as_of_quality: AsOfQuality = "partial"
    as_of_reason: str | None = None
    scd2_as_of: date | None = None
    scd2_available: bool = False
    scd2_note: str | None = None
    scd2_coverage: Scd2CoverageOut = Scd2CoverageOut()
    chain: list[MarketIndustryLevelItemOut] = []
    metrics: MarketIndustryQuoteOut | None = None
    items: list[MarketIndustryMemberOut] = []
