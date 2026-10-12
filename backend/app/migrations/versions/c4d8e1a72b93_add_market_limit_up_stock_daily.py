"""add market_limit_up_stock_daily

Revision ID: c4d8e1a72b93
Revises: a9b3c7d1e5f2
Create Date: 2026-10-12

个股级涨停/炸板/连板事实表（唯一键 trade_date + stock_id）。判定列（is_lu/touched/
is_zb/prev_is_lu/streak/N天M板/missing_days）恒由交易所限价口径写入；东财独有列
（seal_time/seal_fund/break_count）可空，历史回补恒 NULL，绝不填 0。窗口派生列
（days_span/boards_in_window/missing_days）对"炸板-only"（不在候选窗口内）的票无从
计算，同样允许 NULL（缺失 ≠ 0）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4d8e1a72b93"
down_revision: str | None = "a9b3c7d1e5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_limit_up_stock_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sa.String(length=16), nullable=False),
        sa.Column("is_lu", sa.Boolean(), nullable=False),
        sa.Column("touched", sa.Boolean(), nullable=False),
        sa.Column("is_zb", sa.Boolean(), nullable=False),
        sa.Column("prev_is_lu", sa.Boolean(), nullable=False),
        sa.Column("streak", sa.Integer(), nullable=False),
        sa.Column("days_span", sa.Integer(), nullable=True),
        sa.Column("boards_in_window", sa.Integer(), nullable=True),
        sa.Column("missing_days", sa.Integer(), nullable=True),
        sa.Column("seal_time", sa.String(length=8), nullable=True),
        sa.Column("seal_fund", sa.Numeric(precision=20, scale=4), nullable=True),
        sa.Column("break_count", sa.Integer(), nullable=True),
        sa.Column("fact_source", sa.String(length=16), nullable=False),
        sa.Column("enrich_source", sa.String(length=16), nullable=True),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "stock_id", name="uq_limit_up_stock_daily_date_stock"),
    )


def downgrade() -> None:
    op.drop_table("market_limit_up_stock_daily")
