"""add stock price limits

Revision ID: a7c1f0b2d3e4
Revises: cf4b8e317fe5
Create Date: 2026-09-14

交易所口径涨跌停价（TuShare stk_limit 原值）。价格列为 Numeric(12,4) 以与
daily_quotes.close 同型——用 Float 会让 "close >= up_limit - 0.005" 引入浮点转换，
而 0.005 容差本就是为吸收噪声而设。唯一键 (trade_date, stock_id) 已服务
「按日筛 + 窗口 hash join」，不再建第二个索引。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7c1f0b2d3e4"
down_revision: str | None = "cf4b8e317fe5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stock_price_limits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("ts_code", sa.String(length=16), nullable=False),
        sa.Column("pre_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("up_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column("down_limit", sa.Numeric(12, 4), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", "stock_id", name="uq_price_limit_date_stock"),
    )


def downgrade() -> None:
    op.drop_table("stock_price_limits")
