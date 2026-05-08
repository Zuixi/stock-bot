"""add daily_basic_indicators table

Revision ID: a6b7c8d9e0f1
Revises: f5a6b7c8d9e0
Create Date: 2026-05-08 17:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6b7c8d9e0f1"
down_revision: Union[str, None] = "f5a6b7c8d9e0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_basic_indicators",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Numeric(precision=12, scale=4), nullable=True),
        # Market activity
        sa.Column("turnover_rate", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("turnover_rate_f", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("volume_ratio", sa.Numeric(precision=12, scale=4), nullable=True),
        # Valuation
        sa.Column("pe", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("pe_ttm", sa.Numeric(precision=16, scale=4), nullable=True),
        sa.Column("pb", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("ps", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("ps_ttm", sa.Numeric(precision=12, scale=4), nullable=True),
        # Dividend
        sa.Column("dv_ratio", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("dv_ttm", sa.Numeric(precision=12, scale=4), nullable=True),
        # Shares (in 10,000 shares)
        sa.Column("total_share", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("float_share", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("free_share", sa.Numeric(precision=20, scale=4), nullable=True),
        # Market cap (in 10,000 CNY)
        sa.Column("total_mv", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("circ_mv", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stock_id", "trade_date", name="uq_daily_basic_stock_date"
        ),
    )
    op.create_index(
        "idx_daily_basic_stock_date",
        "daily_basic_indicators",
        ["stock_id", "trade_date"],
        unique=False,
    )
    op.create_index(
        "idx_daily_basic_trade_date",
        "daily_basic_indicators",
        ["trade_date"],
        unique=False,
    )
    op.create_index(
        op.f("ix_daily_basic_indicators_stock_id"),
        "daily_basic_indicators",
        ["stock_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_daily_basic_indicators_stock_id"), table_name="daily_basic_indicators")
    op.drop_index("idx_daily_basic_trade_date", table_name="daily_basic_indicators")
    op.drop_index("idx_daily_basic_stock_date", table_name="daily_basic_indicators")
    op.drop_table("daily_basic_indicators")
