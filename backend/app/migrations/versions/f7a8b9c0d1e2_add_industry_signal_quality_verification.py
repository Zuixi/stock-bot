"""add industry signal quality verification persistence

Revision ID: f7a8b9c0d1e2
Revises: e6f7a8b9c0d1
Create Date: 2026-09-03 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "e6f7a8b9c0d1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industry_data_quality_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("signal_ready", sa.Boolean(), nullable=False),
        sa.Column("ready_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("missing_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("stale_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rejected_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("partial_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("details", JSONB, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_key", "as_of", name="uq_industry_quality_date"),
    )
    op.create_index(
        "idx_industry_quality_lookup",
        "industry_data_quality_snapshots",
        ["industry_key", "as_of"],
        unique=False,
    )

    op.create_table(
        "industry_signal_events",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("previous_signal_type", sa.String(length=16), nullable=True),
        sa.Column("previous_phase", sa.String(length=16), nullable=True),
        sa.Column("signal_type", sa.String(length=16), nullable=False),
        sa.Column("phase", sa.String(length=16), nullable=False),
        sa.Column("basis", JSONB, nullable=False),
        sa.Column("basis_periods", JSONB, nullable=False),
        sa.Column("quality_snapshot", JSONB, nullable=False),
        sa.Column("rule_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "industry_key", "event_date", "event_sequence",
            name="uq_industry_signal_event",
        ),
    )
    op.create_index(
        "idx_industry_signal_events_lookup",
        "industry_signal_events",
        ["industry_key", "event_date"],
        unique=False,
    )

    op.create_table(
        "industry_signal_evaluations",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("signal_event_id", sa.Integer(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        sa.Column("methodology_version", sa.String(length=64), nullable=False),
        sa.Column("target_date", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rules", JSONB, nullable=False),
        sa.Column("start_snapshot", JSONB, nullable=False),
        sa.Column("end_snapshot", JSONB, nullable=True),
        sa.Column("criteria_results", JSONB, nullable=True),
        sa.Column("insufficient_reasons", JSONB, nullable=True),
        sa.Column("score", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.text("now()"), nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["signal_event_id"], ["industry_signal_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "signal_event_id", "horizon_days", "methodology_version",
            name="uq_industry_signal_evaluation",
        ),
    )
    op.create_index(
        "idx_industry_signal_evaluations_due",
        "industry_signal_evaluations",
        ["status", "target_date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_industry_signal_evaluations_due",
        table_name="industry_signal_evaluations",
    )
    op.drop_table("industry_signal_evaluations")
    op.drop_index("idx_industry_signal_events_lookup", table_name="industry_signal_events")
    op.drop_table("industry_signal_events")
    op.drop_index(
        "idx_industry_quality_lookup",
        table_name="industry_data_quality_snapshots",
    )
    op.drop_table("industry_data_quality_snapshots")
