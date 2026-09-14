"""add market sentiment daily

Revision ID: b8d2e1c3f4a5
Revises: a7c1f0b2d3e4
Create Date: 2026-09-14

盘后情绪周期聚合派生缓存表。约 10 个数字/交易日，唯一键 (trade_date) 保证
幂等 upsert；逐日明细不落表（daily_quotes 本身就是历史）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8d2e1c3f4a5"
down_revision: str | None = "a7c1f0b2d3e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_sentiment_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("zt_count", sa.Integer(), nullable=False),
        sa.Column("dt_count", sa.Integer(), nullable=False),
        sa.Column("zb_count", sa.Integer(), nullable=False),
        sa.Column("broken_rate", sa.Float(), nullable=True),
        sa.Column("yzt_avg_pct", sa.Float(), nullable=True),
        sa.Column("promo_1to2", sa.Float(), nullable=True),
        sa.Column("promo_1to2_n", sa.Integer(), nullable=False),
        sa.Column("promo_2to3", sa.Float(), nullable=True),
        sa.Column("promo_2to3_n", sa.Integer(), nullable=False),
        sa.Column("max_streak", sa.Integer(), nullable=False),
        sa.Column("max_streak_symbol", sa.String(length=10), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("trade_date", name="uq_sentiment_daily_date"),
    )


def downgrade() -> None:
    op.drop_table("market_sentiment_daily")
