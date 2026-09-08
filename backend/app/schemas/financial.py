"""Response schemas for financial statements, metrics and valuation history."""

from datetime import date, datetime

from pydantic import BaseModel, Field


class MetricValueOut(BaseModel):
    value: float | None = None
    unit: str | None = None
    period_type: str | None = None
    calc_method: str | None = None
    quality: str | None = None
    source: str | None = None


class FinancialSummaryOut(BaseModel):
    exchange: str
    symbol: str
    name: str
    latest_period: date | None = None
    report_type: str | None = None
    ann_date: date | None = None
    as_of: datetime | None = None
    source: str | None = None
    metrics: dict[str, MetricValueOut] = Field(default_factory=dict)


class ReportPeriodOut(BaseModel):
    period: date
    report_type: str
    ann_date: date | None = None
    quality: str | None = None
    source: str | None = None


class IncomeStatementOut(BaseModel):
    period: date
    revenue: float | None = None
    operate_cost: float | None = None
    operate_profit: float | None = None
    total_profit: float | None = None
    n_income: float | None = None
    n_income_attr_p: float | None = None
    deduct_n_income: float | None = None
    sell_exp: float | None = None
    admin_exp: float | None = None
    fin_exp: float | None = None
    rd_exp: float | None = None
    basic_eps: float | None = None
    diluted_eps: float | None = None


class BalanceSheetOut(BaseModel):
    period: date
    total_assets: float | None = None
    total_liab: float | None = None
    total_hldr_eqy_exc_min_int: float | None = None
    total_hldr_eqy_inc_min_int: float | None = None
    money_cap: float | None = None
    accounts_receiv: float | None = None
    inventories: float | None = None
    fix_assets: float | None = None
    intan_assets: float | None = None
    st_borrow: float | None = None
    lt_borrow: float | None = None


class CashFlowOut(BaseModel):
    period: date
    n_cashflow_act: float | None = None
    n_cashflow_inv_act: float | None = None
    n_cashflow_fin_act: float | None = None
    c_cash_equ_end_period: float | None = None


class FinancialStatementsOut(BaseModel):
    exchange: str
    symbol: str
    name: str
    periods: list[ReportPeriodOut] = Field(default_factory=list)
    income_statement: list[IncomeStatementOut] = Field(default_factory=list)
    balance_sheet: list[BalanceSheetOut] = Field(default_factory=list)
    cash_flow: list[CashFlowOut] = Field(default_factory=list)


class MetricHistoryPoint(BaseModel):
    period: date
    value: float | None = None
    unit: str | None = None
    quality: str | None = None


class MetricHistoryOut(BaseModel):
    exchange: str
    symbol: str
    metric_key: str
    unit: str | None = None
    points: list[MetricHistoryPoint] = Field(default_factory=list)


class ValuationStatsOut(BaseModel):
    min: float | None = None
    median: float | None = None
    max: float | None = None
    sample_count: int = 0


class ValuationHistoryOut(BaseModel):
    exchange: str
    symbol: str
    metric: str
    range: str
    as_of: datetime | None = None
    current: float | None = None
    percentiles: dict[str, float | None] = Field(default_factory=dict)
    stats: ValuationStatsOut = Field(default_factory=ValuationStatsOut)
    series: list[dict] = Field(default_factory=list)
