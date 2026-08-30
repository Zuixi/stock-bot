"""add industry research tables

Revision ID: b8c9d0e1f2a3
Revises: a6b7c8d9e0f1
Create Date: 2026-08-31 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8c9d0e1f2a3"
down_revision: Union[str, None] = "a6b7c8d9e0f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "industry_metrics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        # 0 = industry-level; >0 references stocks.id (company-level, P5)
        sa.Column("stock_id", sa.Integer(), server_default="0", nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_tier", sa.String(length=16), nullable=False),
        sa.Column("freq", sa.String(length=16), nullable=False),
        sa.Column("period", sa.Date(), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=True),
        sa.Column("unit", sa.String(length=16), nullable=True),
        sa.Column("extra", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "industry_key", "stock_id", "metric_key", "source", "period",
            name="uq_industry_metrics_key",
        ),
    )
    op.create_index(
        "idx_industry_metrics_lookup",
        "industry_metrics",
        ["industry_key", "metric_key", "period"],
        unique=False,
    )
    op.create_index(
        "idx_industry_metrics_industry",
        "industry_metrics",
        ["industry_key"],
        unique=False,
    )

    op.create_table(
        "industry_reference_points",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Numeric(precision=18, scale=4), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "industry_key", "metric_key", "label", "effective_from",
            name="uq_industry_reference_points",
        ),
    )
    op.create_index(
        "idx_industry_reference_lookup",
        "industry_reference_points",
        ["industry_key", "metric_key", "effective_from"],
        unique=False,
    )

    op.create_table(
        "industry_signals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("signal_type", sa.String(length=16), nullable=False),
        sa.Column("positions", JSONB, nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("basis", JSONB, nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_key", "effective_date", name="uq_industry_signals_date"),
    )
    op.create_index(
        "idx_industry_signals_lookup",
        "industry_signals",
        ["industry_key", "effective_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_industry_signals_lookup", table_name="industry_signals")
    op.drop_table("industry_signals")
    op.drop_index("idx_industry_reference_lookup", table_name="industry_reference_points")
    op.drop_table("industry_reference_points")
    op.drop_index("idx_industry_metrics_industry", table_name="industry_metrics")
    op.drop_index("idx_industry_metrics_lookup", table_name="industry_metrics")
    op.drop_table("industry_metrics")
