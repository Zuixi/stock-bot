"""add freq to industry_metrics unique constraint

Revision ID: c9d0e1f2a3b4
Revises: b8c9d0e1f2a3
Create Date: 2026-09-02 12:00:00.000000

月末既可承载日度观测又可承载月度归档（rollup/派生/演示数据均按 freq 并存），
唯一约束必须纳入 freq —— 否则同批 upsert 出现仅 freq 不同的两行时，
PostgreSQL 抛 "ON CONFLICT DO UPDATE command cannot affect row a second time"。
"""

from typing import Sequence, Union

from alembic import op

revision: str = "c9d0e1f2a3b4"
down_revision: Union[str, None] = "b8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("uq_industry_metrics_key", "industry_metrics", type_="unique")
    op.create_unique_constraint(
        "uq_industry_metrics_key",
        "industry_metrics",
        ["industry_key", "stock_id", "metric_key", "source", "freq", "period"],
    )


def downgrade() -> None:
    op.drop_constraint("uq_industry_metrics_key", "industry_metrics", type_="unique")
    op.create_unique_constraint(
        "uq_industry_metrics_key",
        "industry_metrics",
        ["industry_key", "stock_id", "metric_key", "source", "period"],
    )
