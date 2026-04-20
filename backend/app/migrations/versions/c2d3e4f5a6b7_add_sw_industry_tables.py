"""add sw_industry_classes and sw_industry_members tables

Revision ID: c2d3e4f5a6b7
Revises: a1f2c3d4e5b6
Create Date: 2026-04-20 12:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c2d3e4f5a6b7"
down_revision: Union[str, None] = "a1f2c3d4e5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "sw_industry_classes",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_code", sa.String(10), nullable=False),
        sa.Column("level", sa.Integer(), nullable=False),
        sa.Column("industry_name", sa.String(64), nullable=False),
        sa.Column("parent_code", sa.String(10), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_code", name="uq_sw_class_code"),
    )
    op.create_index("idx_sw_class_level", "sw_industry_classes", ["level"])
    op.create_index("idx_sw_class_parent", "sw_industry_classes", ["parent_code"])

    op.create_table(
        "sw_industry_members",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_code", sa.String(10), nullable=False),
        sa.Column("stock_code", sa.String(16), nullable=False),
        sa.Column("symbol", sa.String(10), nullable=False),
        sa.Column("stock_name", sa.String(64), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_code", "stock_code", name="uq_sw_member_code_stock"),
    )
    op.create_index("idx_sw_member_industry", "sw_industry_members", ["industry_code"])
    op.create_index("idx_sw_member_symbol", "sw_industry_members", ["symbol"])


def downgrade() -> None:
    op.drop_index("idx_sw_member_symbol", table_name="sw_industry_members")
    op.drop_index("idx_sw_member_industry", table_name="sw_industry_members")
    op.drop_table("sw_industry_members")

    op.drop_index("idx_sw_class_parent", table_name="sw_industry_classes")
    op.drop_index("idx_sw_class_level", table_name="sw_industry_classes")
    op.drop_table("sw_industry_classes")
