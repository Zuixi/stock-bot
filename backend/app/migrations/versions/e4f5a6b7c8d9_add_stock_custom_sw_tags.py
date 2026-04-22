"""add stock_custom_sw_tags table

Revision ID: e4f5a6b7c8d9
Revises: d3e4f5a6b7c8
Create Date: 2026-04-22 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4f5a6b7c8d9"
down_revision: Union[str, None] = "d3e4f5a6b7c8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_custom_sw_tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(10), nullable=False),
        sa.Column("industry_code", sa.String(10), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "industry_code", name="uq_custom_sw_tag"),
    )
    op.create_index("idx_custom_sw_tag_symbol", "stock_custom_sw_tags", ["symbol"])
    op.create_index("idx_custom_sw_tag_code", "stock_custom_sw_tags", ["industry_code"])


def downgrade() -> None:
    op.drop_index("idx_custom_sw_tag_code", table_name="stock_custom_sw_tags")
    op.drop_index("idx_custom_sw_tag_symbol", table_name="stock_custom_sw_tags")
    op.drop_table("stock_custom_sw_tags")
