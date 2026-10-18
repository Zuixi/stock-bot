"""add market panorama daily tables (breadth / buckets / industry)

Revision ID: e3f1a7c9d2b4
Revises: c1a4e7b9d2f6
Create Date: 2026-10-19

「A股全景」此前只有当日读面：涨跌分布现算、热力图现算、行业网格只读分类树，
无法回答"今天比昨天变了什么"。本迁移落三张**日级派生表**：

- `market_breadth_daily`：一天一行（上涨/下跌/平盘、涨跌停、中位/均值涨跌幅、成交额）；
- `market_breadth_bucket_daily`：一天 11 行（与 `/market/distribution` 的 11 桶逐字同口径）；
- `market_industry_daily`：一天 31 行（申万一级的涨跌幅/宽度/成交额/涨停数）。

三条口径：
1. 全是 **B 类可回算**（daily_quotes + stock_price_limits），允许回补；`source` 区分
   `local_calc` / `backfill`；
2. 行业归属是**当前申万快照**（C 类），`attribution` 固定写
   `current_sw_snapshot`，UI 必须标注"按当前分类回溯"；
3. 缺失不落 0：无涨停价的早期日子 `limit_up_count` 可为 NULL（回补只从限价安全起点开始）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e3f1a7c9d2b4"
down_revision: str | None = "c1a4e7b9d2f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_breadth_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("up_count", sa.Integer(), nullable=False),
        sa.Column("down_count", sa.Integer(), nullable=False),
        sa.Column("flat_count", sa.Integer(), nullable=False),
        sa.Column("limit_up_count", sa.Integer(), nullable=True),
        sa.Column("limit_down_count", sa.Integer(), nullable=True),
        sa.Column("median_pct", sa.Float(), nullable=True),
        sa.Column("mean_pct", sa.Float(), nullable=True),
        # 千元（与 daily_quotes.amount 同单位），展示层换算
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="local_calc"),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("trade_date", name="uq_market_breadth_daily_date"),
    )
    op.create_index("ix_market_breadth_daily_date", "market_breadth_daily", ["trade_date"])

    op.create_table(
        "market_breadth_bucket_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("bucket", sa.String(16), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.UniqueConstraint(
            "trade_date", "bucket", name="uq_market_breadth_bucket_daily_date_bucket"
        ),
    )
    op.create_index(
        "ix_market_breadth_bucket_daily_date", "market_breadth_bucket_daily", ["trade_date"]
    )

    op.create_table(
        "market_industry_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("industry_code", sa.String(16), nullable=False),
        sa.Column("industry_name", sa.String(32), nullable=False),
        sa.Column("pct_chg", sa.Float(), nullable=True),
        sa.Column("up_count", sa.Integer(), nullable=False),
        sa.Column("down_count", sa.Integer(), nullable=False),
        sa.Column("flat_count", sa.Integer(), nullable=False),
        sa.Column("limit_up_count", sa.Integer(), nullable=True),
        # 千元
        sa.Column("amount", sa.Numeric(20, 4), nullable=True),
        # 上涨家数 / 有行情家数
        sa.Column("breadth", sa.Float(), nullable=True),
        # C 类归属：current_sw_snapshot（当前申万快照回溯）
        sa.Column(
            "attribution", sa.String(32), nullable=False, server_default="current_sw_snapshot"
        ),
        sa.Column("source", sa.String(16), nullable=False, server_default="local_calc"),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "trade_date", "industry_code", name="uq_market_industry_daily_date_code"
        ),
    )
    op.create_index("ix_market_industry_daily_date", "market_industry_daily", ["trade_date"])
    op.create_index(
        "ix_market_industry_daily_code_date",
        "market_industry_daily",
        ["industry_code", "trade_date"],
    )


def downgrade() -> None:
    op.drop_table("market_industry_daily")
    op.drop_table("market_breadth_bucket_daily")
    op.drop_table("market_breadth_daily")
