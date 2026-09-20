"""数据新鲜度巡检（GET /market/data-freshness）响应模型。

字段与 reconciliation_service.reconcile_market_data(apply=False) 的返回
一一对应；日期统一 isoformat 字符串（JSON 直出，前端零解析）。
"""

from __future__ import annotations

from pydantic import BaseModel


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
