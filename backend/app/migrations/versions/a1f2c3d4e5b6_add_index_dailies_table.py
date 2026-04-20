"""add index_dailies table

Revision ID: a1f2c3d4e5b6
Revises: b7c4e2a91d3f
Create Date: 2026-04-20 00:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1f2c3d4e5b6"
down_revision: Union[str, None] = "b7c4e2a91d3f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "index_dailies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts_code", sa.String(16), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("open", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("high", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("low", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("close", sa.Numeric(precision=12, scale=4), nullable=False),
        sa.Column("pre_close", sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column("volume", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column("amount", sa.Numeric(precision=20, scale=2), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ts_code", "trade_date", name="uq_index_dailies_code_date"),
    )
    op.create_index("idx_index_dailies_ts_date", "index_dailies", ["ts_code", "trade_date"])
    op.create_index("idx_index_dailies_date", "index_dailies", ["trade_date"])
    op.create_index(op.f("ix_index_dailies_ts_code"), "index_dailies", ["ts_code"])


def downgrade() -> None:
    op.drop_index(op.f("ix_index_dailies_ts_code"), table_name="index_dailies")
    op.drop_index("idx_index_dailies_date", table_name="index_dailies")
    op.drop_index("idx_index_dailies_ts_date", table_name="index_dailies")
    op.drop_table("index_dailies")
