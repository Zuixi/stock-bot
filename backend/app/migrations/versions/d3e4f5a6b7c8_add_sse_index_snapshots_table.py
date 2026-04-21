"""add sse_index_snapshots table

Revision ID: d3e4f5a6b7c8
Revises: c2d3e4f5a6b7
Create Date: 2026-04-22 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: Union[str, None] = "c2d3e4f5a6b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sse_index_snapshots",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("code", sa.String(10), nullable=False),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("collect_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("prev_close", sa.Numeric(12, 4), nullable=True),
        sa.Column("open", sa.Numeric(12, 4), nullable=True),
        sa.Column("high", sa.Numeric(12, 4), nullable=True),
        sa.Column("low", sa.Numeric(12, 4), nullable=True),
        sa.Column("last", sa.Numeric(12, 4), nullable=False),
        sa.Column("chg_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", "collect_time", name="uq_sse_snapshots_code_time"),
    )
    op.create_index("ix_sse_index_snapshots_code", "sse_index_snapshots", ["code"])
    op.create_index("idx_sse_snapshots_code_date", "sse_index_snapshots", ["code", "trade_date"])
    op.create_index("idx_sse_snapshots_date", "sse_index_snapshots", ["trade_date"])


def downgrade() -> None:
    op.drop_index("idx_sse_snapshots_date", table_name="sse_index_snapshots")
    op.drop_index("idx_sse_snapshots_code_date", table_name="sse_index_snapshots")
    op.drop_index("ix_sse_index_snapshots_code", table_name="sse_index_snapshots")
    op.drop_table("sse_index_snapshots")
