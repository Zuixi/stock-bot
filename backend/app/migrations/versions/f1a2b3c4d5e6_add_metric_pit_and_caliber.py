"""add industry_metrics pit + caliber columns

Revision ID: f1a2b3c4d5e6
Revises: a9b3c7d1e5f2
Create Date: 2026-10-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "a9b3c7d1e5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """只加 2 列复用既有统一指标表（ADR 0013 附带裁定：加列复用，不建新表）。

    published_at   PIT 实际可得时点（ADR 0011）；NULL = 尚未登记（规则/回测由 S2.2 护栏拒绝）
    caliber_version 口径版本；上游口径修订时填写（只要求能力存在，本期不回填具体口径）
    两列均可空：历史行情等"发布即连续可得"的序列不必填。
    不新增索引：既有 unique + idx 已覆盖 scope+metric 时序查询，先量测（spec §S1.2）。
    """
    op.add_column(
        "industry_metrics",
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "industry_metrics",
        sa.Column("caliber_version", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("industry_metrics", "caliber_version")
    op.drop_column("industry_metrics", "published_at")
