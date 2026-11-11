"""add ingest ledger + quarantine + data-quality runs (P5)

Revision ID: a7b8c9d0e1f2
Revises: e3f1a7c9d2b4
Create Date: 2026-11-11

P5 清洗分层的账本层：

- ``ingest_batches``：每次 (domain, business_date, source, capture_kind) 采集的终态。
  ``status='accepted'`` 的记录即"分区封存"——对账下一轮据此跳过外呼（仍会用 DB 行数
  做一次廉价校验，防止封存与真实数据漂移）。
- ``ingest_quarantine``：校验拒绝的行原样留档 + ``reason_code``，替代此前的静默 skip。
- ``data_quality_runs``：声明式期望（SQL 断言）的执行历史。

三张表都只是账本；判据在代码里（``services/ingest_pipeline.py`` /
``services/data_expectations.py``），迁移不引入任何业务默认值。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7b8c9d0e1f2"
down_revision: str | None = "e3f1a7c9d2b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ingest_batches",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("domain", sa.String(length=48), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("capture_kind", sa.String(length=16), nullable=False, server_default="eod_full"),
        sa.Column("params_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_path", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("rows_fetched", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_accepted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_quarantined", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint(
            "domain",
            "business_date",
            "source",
            "capture_kind",
            "params_hash",
            name="uq_ingest_batches_partition",
        ),
    )
    op.create_index("ix_ingest_batches_domain_date", "ingest_batches", ["domain", "business_date"])
    op.create_index("ix_ingest_batches_status", "ingest_batches", ["status", "started_at"])

    op.create_table(
        "ingest_quarantine",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "batch_id",
            sa.BigInteger(),
            sa.ForeignKey("ingest_batches.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("domain", sa.String(length=48), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=False),
        sa.Column("row_key", sa.String(length=64), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_ingest_quarantine_domain_date",
        "ingest_quarantine",
        ["domain", "business_date"],
    )
    op.create_index(
        "ix_ingest_quarantine_reason",
        "ingest_quarantine",
        ["reason_code", "created_at"],
    )

    op.create_table(
        "data_quality_runs",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("expectation_key", sa.String(length=96), nullable=False),
        sa.Column("domain", sa.String(length=48), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=True),
        sa.Column("severity", sa.String(length=8), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("observed", sa.Numeric(20, 4), nullable=True),
        sa.Column("threshold", sa.Numeric(20, 4), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "checked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_data_quality_runs_key_checked",
        "data_quality_runs",
        ["expectation_key", "checked_at"],
    )
    op.create_index("ix_data_quality_runs_run", "data_quality_runs", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_data_quality_runs_run", table_name="data_quality_runs")
    op.drop_index("ix_data_quality_runs_key_checked", table_name="data_quality_runs")
    op.drop_table("data_quality_runs")
    op.drop_index("ix_ingest_quarantine_reason", table_name="ingest_quarantine")
    op.drop_index("ix_ingest_quarantine_domain_date", table_name="ingest_quarantine")
    op.drop_table("ingest_quarantine")
    op.drop_index("ix_ingest_batches_status", table_name="ingest_batches")
    op.drop_index("ix_ingest_batches_domain_date", table_name="ingest_batches")
    op.drop_table("ingest_batches")
