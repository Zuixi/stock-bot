"""add stock_user_tags table

Revision ID: f5a6b7c8d9e0
Revises: e4f5a6b7c8d9
Create Date: 2026-04-23 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f5a6b7c8d9e0"
down_revision: Union[str, None] = "e4f5a6b7c8d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "stock_user_tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("symbol", sa.String(10), nullable=False),
        sa.Column("tag_name", sa.String(64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("symbol", "tag_name", name="uq_stock_user_tag"),
    )
    op.create_index("idx_user_tag_symbol", "stock_user_tags", ["symbol"])
    op.create_index("idx_user_tag_name", "stock_user_tags", ["tag_name"])


def downgrade() -> None:
    op.drop_index("idx_user_tag_name", table_name="stock_user_tags")
    op.drop_index("idx_user_tag_symbol", table_name="stock_user_tags")
    op.drop_table("stock_user_tags")
