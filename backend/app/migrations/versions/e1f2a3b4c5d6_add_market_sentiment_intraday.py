"""add market_sentiment_intraday (盘中分时序列缓存)

Revision ID: e1f2a3b4c5d6
Revises: d7c8b9a0e1f2
Create Date: 2026-09-17

Task 12: 盘中分时序列缓存表—— ``scheduler 5min 轮询 → 端点按 captured_at 升序返回``。
落库一份纯池行计数（zt/dt/zb/max_streak）的目的：

- **消费侧免重算**：前端每 30s 轮询 ``/market/sentiment/intraday`` 时只查 SQL，
  不重跑东财池抓取。5min 轮询节拍 + 端点 60s cache 双重兜底。
- **跨重启不丢点**：5min 一次 ``ON CONFLICT (trade_date, captured_at) DO NOTHING``
  幂等入同一秒/同一时间戳的点（重启追跑、coalesce 合并都安全）。
- **历史回放**：与 ``market_sentiment_daily`` 是并列的派生缓存；表本身可整日删
  不会丢真相（真源是 daily_quotes + 实时东财池），可随时由 5min 轮询重建。

为什么 columns = 4 个计数 + 1 个 streak + 主键/日期/时间戳即可：
pool 行数即 zt_count，dt/zb 盘中无独立信号源（无 daily_quotes partial day）→ 0；
max_streak 由 streak>0 候选直接 max 而来。其它（broken_rate、yzt_avg_pct 等）
盘中无"昨日→今日"语义，无信号——见 intraday_sentiment_service.build_intraday_snapshot
的 kpis 注释。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e1f2a3b4c5d6"
down_revision: str | None = "d7c8b9a0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLE_NAME = "market_sentiment_intraday"
_UNIQUE_NAME = "uq_market_sentiment_intraday_date_captured"


def upgrade() -> None:
    op.create_table(
        _TABLE_NAME,
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("zt_count", sa.Integer(), nullable=False),
        sa.Column("dt_count", sa.Integer(), nullable=False),
        sa.Column("zb_count", sa.Integer(), nullable=False),
        sa.Column("max_streak", sa.Integer(), nullable=False),
        sa.UniqueConstraint("trade_date", "captured_at", name=_UNIQUE_NAME),
    )


def downgrade() -> None:
    op.drop_table(_TABLE_NAME)
