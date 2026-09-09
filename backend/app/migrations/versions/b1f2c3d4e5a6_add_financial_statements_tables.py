"""add financial statements tables

Revision ID: b1f2c3d4e5a6
Revises: e6f7a8b9c0d1
Create Date: 2026-09-09 09:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1f2c3d4e5a6"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ── financial_raw_records ──────────────────────────────────────────
    op.create_table(
        "financial_raw_records",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("dataset", sa.String(length=64), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("ts_code", sa.String(length=24), nullable=True),
        sa.Column("request_params", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("source_record_key", sa.String(length=128), nullable=True),
        sa.Column("payload", sa.dialects.postgresql.JSONB(), nullable=True),
        sa.Column("payload_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source",
            "dataset",
            "source_record_key",
            "payload_hash",
            name="uq_financial_raw_record",
        ),
    )
    op.create_index(
        "idx_financial_raw_source_dataset", "financial_raw_records", ["source", "dataset"]
    )
    op.create_index("idx_financial_raw_stock", "financial_raw_records", ["stock_id"])

    # ── financial_report_versions ──────────────────────────────────────
    op.create_table(
        "financial_report_versions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("ts_code", sa.String(length=24), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("report_type", sa.String(length=4), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("f_ann_date", sa.Date(), nullable=True),
        sa.Column("comp_type", sa.String(length=8), server_default="1", nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("source_record_key", sa.String(length=128), nullable=True),
        sa.Column("update_flag", sa.String(length=8), nullable=True),
        sa.Column("currency", sa.String(length=8), server_default="CNY", nullable=False),
        sa.Column(
            "quality_status", sa.String(length=16), server_default="reported", nullable=False
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["stock_id"], ["stocks.id"], name="fk_fin_report_stock"),
        sa.UniqueConstraint(
            "stock_id",
            "end_date",
            "report_type",
            "comp_type",
            "source",
            name="uq_financial_report_version",
        ),
    )
    op.create_index(
        "idx_financial_report_stock_date", "financial_report_versions", ["stock_id", "end_date"]
    )
    op.create_index("idx_financial_report_ann_date", "financial_report_versions", ["ann_date"])

    # ── income_statement_facts ─────────────────────────────────────────
    op.create_table(
        "income_statement_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("revenue", sa.Numeric(20, 2), nullable=True),
        sa.Column("operate_cost", sa.Numeric(20, 2), nullable=True),
        sa.Column("operate_profit", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_profit", sa.Numeric(20, 2), nullable=True),
        sa.Column("n_income", sa.Numeric(20, 2), nullable=True),
        sa.Column("n_income_attr_p", sa.Numeric(20, 2), nullable=True),
        sa.Column("deduct_n_income", sa.Numeric(20, 2), nullable=True),
        sa.Column("sell_exp", sa.Numeric(20, 2), nullable=True),
        sa.Column("admin_exp", sa.Numeric(20, 2), nullable=True),
        sa.Column("fin_exp", sa.Numeric(20, 2), nullable=True),
        sa.Column("rd_exp", sa.Numeric(20, 2), nullable=True),
        sa.Column("basic_eps", sa.Numeric(16, 6), nullable=True),
        sa.Column("diluted_eps", sa.Numeric(16, 6), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["report_version_id"],
            ["financial_report_versions.id"],
            name="fk_income_stmt_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("report_version_id", name="uq_income_statement_facts_ver"),
    )

    # ── balance_sheet_facts ────────────────────────────────────────────
    op.create_table(
        "balance_sheet_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("total_assets", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_liab", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_hldr_eqy_exc_min_int", sa.Numeric(20, 2), nullable=True),
        sa.Column("total_hldr_eqy_inc_min_int", sa.Numeric(20, 2), nullable=True),
        sa.Column("money_cap", sa.Numeric(20, 2), nullable=True),
        sa.Column("accounts_receiv", sa.Numeric(20, 2), nullable=True),
        sa.Column("inventories", sa.Numeric(20, 2), nullable=True),
        sa.Column("fix_assets", sa.Numeric(20, 2), nullable=True),
        sa.Column("intan_assets", sa.Numeric(20, 2), nullable=True),
        sa.Column("st_borrow", sa.Numeric(20, 2), nullable=True),
        sa.Column("lt_borrow", sa.Numeric(20, 2), nullable=True),
        sa.Column("st_note_payable", sa.Numeric(20, 2), nullable=True),
        sa.Column("bond_payable", sa.Numeric(20, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["report_version_id"],
            ["financial_report_versions.id"],
            name="fk_balance_stmt_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("report_version_id", name="uq_balance_sheet_facts_ver"),
    )

    # ── cash_flow_statement_facts ──────────────────────────────────────
    op.create_table(
        "cash_flow_statement_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("n_cashflow_act", sa.Numeric(20, 2), nullable=True),
        sa.Column("n_cashflow_inv_act", sa.Numeric(20, 2), nullable=True),
        sa.Column("n_cashflow_fin_act", sa.Numeric(20, 2), nullable=True),
        sa.Column("c_cash_equ_end_period", sa.Numeric(20, 2), nullable=True),
        sa.Column("c_paid_goods_s", sa.Numeric(20, 2), nullable=True),
        sa.Column("c_paid_to_for_empl", sa.Numeric(20, 2), nullable=True),
        sa.Column("c_paid_for_taxes", sa.Numeric(20, 2), nullable=True),
        sa.Column("c_recp_from_release_sale_sg", sa.Numeric(20, 2), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["report_version_id"],
            ["financial_report_versions.id"],
            name="fk_cashflow_stmt_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("report_version_id", name="uq_cash_flow_statement_facts_ver"),
    )

    # ── financial_metrics ──────────────────────────────────────────────
    op.create_table(
        "financial_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=True),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(20, 6), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("period_type", sa.String(length=16), nullable=True),
        sa.Column(
            "calc_method",
            sa.String(length=32),
            server_default="reported_by_provider",
            nullable=False,
        ),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column(
            "quality_status", sa.String(length=16), server_default="reported", nullable=False
        ),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["report_version_id"],
            ["financial_report_versions.id"],
            name="fk_financial_metric_version",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "stock_id",
            "report_version_id",
            "metric_key",
            name="uq_financial_metric_key",
        ),
        sa.CheckConstraint(
            "quality_status IN ('reported', 'revised', 'derived', 'estimated', 'invalid')",
            name="chk_fin_quality_status",
        ),
    )
    op.create_index(
        "idx_financial_metric_stock_key", "financial_metrics", ["stock_id", "metric_key"]
    )


def downgrade() -> None:
    op.drop_index("idx_financial_metric_stock_key", table_name="financial_metrics")
    op.drop_table("financial_metrics")
    op.drop_table("cash_flow_statement_facts")
    op.drop_table("balance_sheet_facts")
    op.drop_table("income_statement_facts")
    op.drop_index("idx_financial_report_ann_date", table_name="financial_report_versions")
    op.drop_index("idx_financial_report_stock_date", table_name="financial_report_versions")
    op.drop_table("financial_report_versions")
    op.drop_index("idx_financial_raw_stock", table_name="financial_raw_records")
    op.drop_index("idx_financial_raw_source_dataset", table_name="financial_raw_records")
    op.drop_table("financial_raw_records")
