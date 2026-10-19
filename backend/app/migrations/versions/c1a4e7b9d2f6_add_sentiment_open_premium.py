"""add sentiment open premium + yzt sample size

Revision ID: c1a4e7b9d2f6
Revises: b1c8d4e7f2a9
Create Date: 2026-10-18

`market_sentiment_daily` 此前只冻结了 8 个聚合数字，但情绪温度计有 9 张卡：

- **今开溢价均值**（`yzt_avg_open_premium`）每天在快照里算出、却从未落库 ⇒ 这张卡
  没有历史，用户无法追踪；
- **昨日涨停样本数**（`yzt_n`）被卡片用作 `n=36` 的诚实标注，同样未落库 ⇒ 详情页
  无法逐日区分"均值真的变了"与"样本变小了"。

两列都是 B 类（可由 daily_quotes + stock_price_limits 重算），允许回填：
`backend/scripts/backfill_sentiment_metrics.py` 只补 NULL 行、不覆写既有事实。

`yzt_n` 用 `server_default='0'`：历史行没有样本数，0 与"当日没有昨日涨停股"同义；
`yzt_avg_open_premium` 保持 NULL 允许（缺失 ≠ 0%，0% 是"开盘恰好平昨收"的事实断言）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c1a4e7b9d2f6"
down_revision: str | None = "b1c8d4e7f2a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "market_sentiment_daily", sa.Column("yzt_avg_open_premium", sa.Float(), nullable=True)
    )
    op.add_column(
        "market_sentiment_daily",
        sa.Column("yzt_n", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )


def downgrade() -> None:
    op.drop_column("market_sentiment_daily", "yzt_n")
    op.drop_column("market_sentiment_daily", "yzt_avg_open_premium")
