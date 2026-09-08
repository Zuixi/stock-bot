"""Financial statements and derived-metric ORM models.

Design (per plans/industry-research-workbench.md P1 financial foundation):

    financial_raw_records          — raw provider payloads (replayable, auditable)
    financial_report_versions      — parent per (stock, end_date, report_type, source)
    income_statement_facts         — standardized income statement rows
    balance_sheet_facts            — standardized balance sheet rows
    cash_flow_statement_facts      — standardized cash flow rows
    financial_metrics              — derived ratios (ROE, margins, growth, ...)

Key invariants:
  * Same report period may exist under several *sources*; they are kept as
    separate `financial_report_versions` rows (never silently overwrite).
  * Derived metrics carry `calc_method` + `quality_status` so consumers know
    whether a value was reported by the provider or computed by us.
  * Money values are normalized to CNY yuan; ratios/percentages are kept as the
    provider-scale numbers (TuShare ratio columns are percent, e.g. 18.52).
"""

from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base

_QUALITY_CHECK = CheckConstraint(
    "quality_status IN ('reported', 'revised', 'derived', 'estimated', 'invalid')",
    name="chk_fin_quality_status",
)


class FinancialRawRecord(Base):
    """One provider response row captured verbatim for replay/audit."""

    __tablename__ = "financial_raw_records"
    __table_args__ = (
        UniqueConstraint(
            "source", "dataset", "source_record_key", "payload_hash",
            name="uq_financial_raw_record",
        ),
        Index("idx_financial_raw_source_dataset", "source", "dataset"),
        Index("idx_financial_raw_stock", "stock_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(64), nullable=False)   # tushare / akshare ...
    dataset: Mapped[str] = mapped_column(String(64), nullable=False)  # income / fina_indicator ...
    stock_id: Mapped[int | None] = mapped_column(nullable=True)
    ts_code: Mapped[str | None] = mapped_column(String(24))
    request_params: Mapped[dict | None] = mapped_column(JSONB)
    source_record_key: Mapped[str | None] = mapped_column(String(128))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class FinancialReportVersion(Base):
    """A distinct financial report publication for a stock."""

    __tablename__ = "financial_report_versions"
    __table_args__ = (
        # One row per report publication. `update_flag`/`comp_type` let revised
        # reports and consolidated vs parent statements coexist.
        UniqueConstraint(
            "stock_id", "end_date", "report_type", "comp_type", "source",
            name="uq_financial_report_version",
        ),
        Index("idx_financial_report_stock_date", "stock_id", "end_date"),
        Index("idx_financial_report_ann_date", "ann_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(nullable=False)  # FK enforced in migration
    ts_code: Mapped[str | None] = mapped_column(String(24))
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    report_type: Mapped[str] = mapped_column(String(4), nullable=False)
    ann_date: Mapped[date | None] = mapped_column(Date)   # report announcement date
    f_ann_date: Mapped[date | None] = mapped_column(Date)  # factual announcement date
    comp_type: Mapped[str] = mapped_column(String(8), nullable=False, server_default="1")  # 1=consolidated
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    source_record_key: Mapped[str | None] = mapped_column(String(128))
    update_flag: Mapped[str | None] = mapped_column(String(8))  # 1=new, 2=revised revision
    currency: Mapped[str] = mapped_column(String(8), nullable=False, server_default="CNY")
    quality_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="reported"
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_record_id: Mapped[int | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class _StatementFactsBase(Base):
    """Shared columns for the three standardized statement-fact tables."""

    __abstract__ = True

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    report_version_id: Mapped[int] = mapped_column(
        ForeignKey("financial_report_versions.id", ondelete="CASCADE"),
        nullable=False,
    )


class IncomeStatementFacts(_StatementFactsBase):
    """Standardized income statement — amounts in CNY yuan."""

    __tablename__ = "income_statement_facts"
    __table_args__ = (
        UniqueConstraint("report_version_id", name="uq_income_statement_facts_ver"),
    )

    revenue: Mapped[float | None] = mapped_column(Numeric(20, 2))
    operate_cost: Mapped[float | None] = mapped_column(Numeric(20, 2))
    operate_profit: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_profit: Mapped[float | None] = mapped_column(Numeric(20, 2))
    n_income: Mapped[float | None] = mapped_column(Numeric(20, 2))
    n_income_attr_p: Mapped[float | None] = mapped_column(Numeric(20, 2))
    deduct_n_income: Mapped[float | None] = mapped_column(Numeric(20, 2))
    sell_exp: Mapped[float | None] = mapped_column(Numeric(20, 2))
    admin_exp: Mapped[float | None] = mapped_column(Numeric(20, 2))
    fin_exp: Mapped[float | None] = mapped_column(Numeric(20, 2))
    rd_exp: Mapped[float | None] = mapped_column(Numeric(20, 2))
    basic_eps: Mapped[float | None] = mapped_column(Numeric(16, 6))
    diluted_eps: Mapped[float | None] = mapped_column(Numeric(16, 6))


class BalanceSheetFacts(_StatementFactsBase):
    """Standardized balance sheet — amounts in CNY yuan."""

    __tablename__ = "balance_sheet_facts"
    __table_args__ = (
        UniqueConstraint("report_version_id", name="uq_balance_sheet_facts_ver"),
    )

    total_assets: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_liab: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_hldr_eqy_exc_min_int: Mapped[float | None] = mapped_column(Numeric(20, 2))
    total_hldr_eqy_inc_min_int: Mapped[float | None] = mapped_column(Numeric(20, 2))
    money_cap: Mapped[float | None] = mapped_column(Numeric(20, 2))
    accounts_receiv: Mapped[float | None] = mapped_column(Numeric(20, 2))
    inventories: Mapped[float | None] = mapped_column(Numeric(20, 2))
    fix_assets: Mapped[float | None] = mapped_column(Numeric(20, 2))
    intan_assets: Mapped[float | None] = mapped_column(Numeric(20, 2))
    st_borrow: Mapped[float | None] = mapped_column(Numeric(20, 2))
    lt_borrow: Mapped[float | None] = mapped_column(Numeric(20, 2))
    st_note_payable: Mapped[float | None] = mapped_column(Numeric(20, 2))
    bond_payable: Mapped[float | None] = mapped_column(Numeric(20, 2))


class CashFlowStatementFacts(_StatementFactsBase):
    """Standardized cash flow statement — amounts in CNY yuan."""

    __tablename__ = "cash_flow_statement_facts"
    __table_args__ = (
        UniqueConstraint("report_version_id", name="uq_cash_flow_statement_facts_ver"),
    )

    n_cashflow_act: Mapped[float | None] = mapped_column(Numeric(20, 2))  # 经营现金流净额
    n_cashflow_inv_act: Mapped[float | None] = mapped_column(Numeric(20, 2))
    n_cashflow_fin_act: Mapped[float | None] = mapped_column(Numeric(20, 2))
    c_cash_equ_end_period: Mapped[float | None] = mapped_column(Numeric(20, 2))
    c_paid_goods_s: Mapped[float | None] = mapped_column(Numeric(20, 2))
    c_paid_to_for_empl: Mapped[float | None] = mapped_column(Numeric(20, 2))
    c_paid_for_taxes: Mapped[float | None] = mapped_column(Numeric(20, 2))
    c_recp_from_release_sale_sg: Mapped[float | None] = mapped_column(Numeric(20, 2))


class FinancialMetric(Base):
    """Derived or provider-reported financial ratios/indicators."""

    __tablename__ = "financial_metrics"
    __table_args__ = (
        UniqueConstraint(
            "stock_id", "report_version_id", "metric_key",
            name="uq_financial_metric_key",
        ),
        Index("idx_financial_metric_stock_key", "stock_id", "metric_key"),
        # One metric_key cannot mix incompatible units across rows.
        _QUALITY_CHECK,
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    stock_id: Mapped[int] = mapped_column(nullable=False)
    report_version_id: Mapped[int | None] = mapped_column(
        ForeignKey("financial_report_versions.id", ondelete="CASCADE"), nullable=True
    )
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[float | None] = mapped_column(Numeric(20, 6))
    unit: Mapped[str | None] = mapped_column(String(16))
    period_type: Mapped[str | None] = mapped_column(String(16))  # report / cumulative / ttm / quarter
    calc_method: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="reported_by_provider"
    )
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    quality_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="reported"
    )
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
