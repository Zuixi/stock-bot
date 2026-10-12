"""Market-data face response schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class GlobalIndexCardOut(BaseModel):
    ts_code: str
    name: str
    market: str
    region: str
    price: float | None = None
    change: float | None = None
    pct_change: float | None = None
    spark: list[float] = Field(default_factory=list)
    updated_at: datetime
    source: str


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
    date: str
    net_amount: float | None = None  # 万元


class NorthboundSeriesOut(BaseModel):
    """北向净流入序列 envelope；上游停更时由 `source_status` 显式标注。"""

    as_of: str | None = None  # ISO 日期（返回 items 末项的日）；无 items 时 None
    stale_days: int | None = None  # 自然日差（今天 - as_of）；无 items 时 None
    source_status: Literal["live", "discontinued"] = "discontinued"
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
    extra_days: list[str] = []  # 区间外/非交易日的行（未来脏行；不参与覆盖率）
    first_day: str | None = None
    last_day: str | None = None
    status: str = "ok"  # ok | missing_table | missing_column
    # 分母口径降级：主源 `daily_quotes` 不可用（未建/迁移未应用）→ 交易日只来自
    # `stock_price_limits`。换源必须可见，不能静默换一个分母。
    # 名字与 reconcile 的 `degraded_calendar`（trade_cal 外呼降级）**故意区分**：一个是
    # "本地日历源换了"，一个是"tushare 日历降级成工作日启发式"。
    degraded_trading_calendar: bool = False


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
