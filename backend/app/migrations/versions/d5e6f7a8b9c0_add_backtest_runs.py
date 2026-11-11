"""add backtest_runs (spec §S2.3)

Revision ID: d5e6f7a8b9c0
Revises: c3d4e5f6a7b8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d5e6f7a8b9c0"
down_revision: str | None = "c3d4e5f6a7b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "backtest_runs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("rule_key", sa.String(length=32), nullable=False),
        sa.Column("rule_version", sa.String(length=32), nullable=False),
        sa.Column("scope_type", sa.String(length=16), nullable=False),
        sa.Column("scope_keys", postgresql.ARRAY(sa.String(length=32)), nullable=False),
        sa.Column("window_from", sa.Date(), nullable=False),
        sa.Column("window_to", sa.Date(), nullable=False),
        sa.Column("horizons", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("benchmark", sa.String(length=16), nullable=False),
        sa.Column("split", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("pit_enforced", sa.Boolean(), nullable=False),
        sa.Column("stats", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_backtest_runs_rule", "backtest_runs", ["rule_key", "window_to"])


def downgrade() -> None:
    op.drop_index("idx_backtest_runs_rule", table_name="backtest_runs")
    op.drop_table("backtest_runs")
