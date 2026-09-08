"""Query services for financial statements, metrics and valuation history."""

from __future__ import annotations

import logging
import statistics
from datetime import date, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.financial import FinancialMetric, FinancialReportVersion
from app.repositories import daily_basic_repo, financial_repo, stock_repo
from app.schemas.financial import (
    BalanceSheetOut,
    CashFlowOut,
    FinancialStatementsOut,
    FinancialSummaryOut,
    IncomeStatementOut,
    MetricHistoryOut,
    MetricValueOut,
    ReportPeriodOut,
    ValuationHistoryOut,
    ValuationStatsOut,
)

logger = logging.getLogger(__name__)

_RANGE_DAYS = {"1y": 365, "3y": 1095, "5y": 1825}

_VALUATION_METRICS = {
    "pe_ttm": "pe_ttm",
    "pe": "pe",
    "pb": "pb",
    "ps_ttm": "ps_ttm",
    "ps": "ps",
    "dividend_yield": "dv_ratio",
}

# Description of key metrics surfaced in the summary (order matters for UI).
SUMMARY_KEYS = [
    "roe", "gross_margin", "net_margin", "debt_to_asset",
    "current_ratio", "ocf_to_net_profit", "revenue_yoy", "profit_yoy",
    "eps", "bps",
]


async def _resolve_stock(db: AsyncSession, exchange: str, symbol: str):
    stock = await stock_repo.get_stock_by_symbol(db, exchange, symbol)
    if stock is None:
        raise LookupError(f"Stock not found: {exchange}/{symbol}")
    return stock


async def get_financial_summary(
    db: AsyncSession, exchange: str, symbol: str
) -> FinancialSummaryOut:
    stock = await _resolve_stock(db, exchange, symbol)
    version = await financial_repo.get_latest_report_version(db, stock.id)
    if version is None:
        return FinancialSummaryOut(exchange=exchange, symbol=symbol, name=stock.name)

    metrics = await financial_repo.list_metrics(db, stock.id)
    version_metrics = {
        m.metric_key: m for m in metrics
        if m.report_version_id == version.id
    }

    metric_out: dict[str, MetricValueOut] = {}
    for key in SUMMARY_KEYS:
        m = version_metrics.get(key)
        if m is None:
            continue
        metric_out[key] = MetricValueOut(
            value=float(m.value) if m.value is not None else None,
            unit=m.unit,
            period_type=m.period_type,
            calc_method=m.calc_method,
            quality=m.quality_status,
            source=m.source,
        )

    return FinancialSummaryOut(
        exchange=exchange,
        symbol=symbol,
        name=stock.name,
        latest_period=version.end_date,
        report_type=version.report_type,
        ann_date=version.ann_date,
        as_of=version.as_of,
        source=version.source,
        metrics=metric_out,
    )


async def get_financial_statements(
    db: AsyncSession,
    exchange: str,
    symbol: str,
    period_count: int = 8,
) -> FinancialStatementsOut:
    stock = await _resolve_stock(db, exchange, symbol)
    versions = await financial_repo.list_report_versions(db, stock.id, limit=period_count)
    versions = list(reversed(versions))  # ascending for charting

    out = FinancialStatementsOut(exchange=exchange, symbol=symbol, name=stock.name)
    for version in versions:
        income, balance, cashflow = await financial_repo.get_statement_facts(db, version.id)
        out.periods.append(
            ReportPeriodOut(
                period=version.end_date,
                report_type=version.report_type,
                ann_date=version.ann_date,
                quality=version.quality_status,
                source=version.source,
            )
        )
        if income is not None:
            out.income_statement.append(
                IncomeStatementOut(
                    period=version.end_date,
                    revenue=float(income.revenue) if income.revenue is not None else None,
                    operate_cost=float(income.operate_cost) if income.operate_cost is not None else None,
                    operate_profit=float(income.operate_profit) if income.operate_profit is not None else None,
                    total_profit=float(income.total_profit) if income.total_profit is not None else None,
                    n_income=float(income.n_income) if income.n_income is not None else None,
                    n_income_attr_p=float(income.n_income_attr_p) if income.n_income_attr_p is not None else None,
                    deduct_n_income=float(income.deduct_n_income) if income.deduct_n_income is not None else None,
                    sell_exp=float(income.sell_exp) if income.sell_exp is not None else None,
                    admin_exp=float(income.admin_exp) if income.admin_exp is not None else None,
                    fin_exp=float(income.fin_exp) if income.fin_exp is not None else None,
                    rd_exp=float(income.rd_exp) if income.rd_exp is not None else None,
                    basic_eps=float(income.basic_eps) if income.basic_eps is not None else None,
                    diluted_eps=float(income.diluted_eps) if income.diluted_eps is not None else None,
                )
            )
        if balance is not None:
            out.balance_sheet.append(
                BalanceSheetOut(
                    period=version.end_date,
                    total_assets=float(balance.total_assets) if balance.total_assets is not None else None,
                    total_liab=float(balance.total_liab) if balance.total_liab is not None else None,
                    total_hldr_eqy_exc_min_int=float(balance.total_hldr_eqy_exc_min_int)
                    if balance.total_hldr_eqy_exc_min_int is not None else None,
                    total_hldr_eqy_inc_min_int=float(balance.total_hldr_eqy_inc_min_int)
                    if balance.total_hldr_eqy_inc_min_int is not None else None,
                    money_cap=float(balance.money_cap) if balance.money_cap is not None else None,
                    accounts_receiv=float(balance.accounts_receiv) if balance.accounts_receiv is not None else None,
                    inventories=float(balance.inventories) if balance.inventories is not None else None,
                    fix_assets=float(balance.fix_assets) if balance.fix_assets is not None else None,
                    intan_assets=float(balance.intan_assets) if balance.intan_assets is not None else None,
                    st_borrow=float(balance.st_borrow) if balance.st_borrow is not None else None,
                    lt_borrow=float(balance.lt_borrow) if balance.lt_borrow is not None else None,
                )
            )
        if cashflow is not None:
            out.cash_flow.append(
                CashFlowOut(
                    period=version.end_date,
                    n_cashflow_act=float(cashflow.n_cashflow_act) if cashflow.n_cashflow_act is not None else None,
                    n_cashflow_inv_act=float(cashflow.n_cashflow_inv_act)
                    if cashflow.n_cashflow_inv_act is not None else None,
                    n_cashflow_fin_act=float(cashflow.n_cashflow_fin_act)
                    if cashflow.n_cashflow_fin_act is not None else None,
                    c_cash_equ_end_period=float(cashflow.c_cash_equ_end_period)
                    if cashflow.c_cash_equ_end_period is not None else None,
                )
            )
    return out


async def get_metrics_history(
    db: AsyncSession,
    exchange: str,
    symbol: str,
    metric_keys: list[str],
) -> list[MetricHistoryOut]:
    stock = await _resolve_stock(db, exchange, symbol)
    rows = await financial_repo.list_metrics_series(db, stock.id, metric_keys=metric_keys)

    by_key: dict[str, MetricHistoryOut] = {}
    for metric, end_date in rows:
        if metric.value is None:
            continue
        out = by_key.setdefault(
            metric.metric_key,
            MetricHistoryOut(
                exchange=exchange, symbol=symbol,
                metric_key=metric.metric_key, unit=metric.unit,
            ),
        )
        out.points.append(
            {
                "period": end_date,
                "value": float(metric.value),
                "unit": metric.unit,
                "quality": metric.quality_status,
            }
        )
    return list(by_key.values())


async def get_valuation_history(
    db: AsyncSession,
    exchange: str,
    symbol: str,
    metric: str,
    range_: str = "3y",
) -> ValuationHistoryOut:
    """Compute a valuation percentile band from daily_basic history.

    Percentiles are computed over *valid positive* samples only: negative/zero
    values (e.g. negative PE from losses) and missing values are excluded so a
    loss-making period is never mistaken for an extreme-low valuation.
    """
    attr = _VALUATION_METRICS.get(metric)
    if attr is None:
        raise ValueError(f"Unsupported valuation metric: {metric}")

    stock = await _resolve_stock(db, exchange, symbol)
    end = date.today()
    days = _RANGE_DAYS.get(range_, 1095)
    start = end - timedelta(days=days)

    rows = await daily_basic_repo.get_daily_basic(db, stock.id, start_date=start, end_date=end)

    valid: list[tuple[date, float]] = []
    for row in rows:
        value = getattr(row, attr, None)
        if value is None:
            continue
        value = float(value)
        if value <= 0:  # negative/missing → excluded from percentile basis
            continue
        valid.append((row.trade_date, value))

    series = [{"trade_date": d.isoformat(), "value": v} for d, v in valid]
    values = [v for _, v in valid]

    stats = ValuationStatsOut()
    percentiles: dict[str, float | None] = {}
    for r in _RANGE_DAYS:
        window_start = end - timedelta(days=_RANGE_DAYS[r])
        window_values = [v for d, v in valid if d >= window_start]
        current = window_values[-1] if window_values else None
        pct = _percentile_rank(current, window_values) if current is not None else None
        percentiles[r] = pct

    if values:
        stats = ValuationStatsOut(
            min=min(values),
            median=statistics.median(values),
            max=max(values),
            sample_count=len(values),
        )

    current = valid[-1][1] if valid else None
    current_pct = percentiles.get(range_)

    return ValuationHistoryOut(
        exchange=exchange,
        symbol=symbol,
        metric=metric,
        range=range_,
        as_of=datetime.now(),
        current=current,
        percentiles=percentiles,
        stats=stats,
        series=series,
    )


def _percentile_rank(current: float | None, values: list[float]) -> float | None:
    """Return the percentile rank (0-100) of *current* within *values*."""
    if current is None or not values:
        return None
    below = sum(1 for v in values if v <= current)
    return round(below / len(values) * 100.0, 1)
