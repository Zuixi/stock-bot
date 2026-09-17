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
