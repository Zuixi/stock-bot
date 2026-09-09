"""add user ownership to stock_user_tags, tasks, and create user_watchlists tables

Revision ID: 5a1b2c3d4e5f
Revises: 49741053b341
Create Date: 2026-09-09 16:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "5a1b2c3d4e5f"
down_revision: str | None = "49741053b341"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. Update stock_user_tags with user_id and composite unique constraint
    op.add_column(
        "stock_user_tags",
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            server_default=sa.text("'00000000-0000-0000-0000-000000000000'::uuid"),
        ),
    )
    # Remove default after backfilling existing rows
    op.alter_column("stock_user_tags", "user_id", server_default=None)

    op.drop_constraint("uq_stock_user_tag", "stock_user_tags", type_="unique")
    op.create_unique_constraint(
        "uq_stock_user_tag",
        "stock_user_tags",
        ["user_id", "symbol", "tag_name"],
    )
    op.create_index("idx_user_tag_user_id", "stock_user_tags", ["user_id"])

    # 2. Create user_watchlists table
    op.create_table(
        "user_watchlists",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False, server_default="默认自选"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.UniqueConstraint("user_id", "name", name="uq_user_watchlist_name"),
    )
    op.create_index("idx_user_watchlists_user_id", "user_watchlists", ["user_id"])

    # 3. Create user_watchlist_items table
    op.create_table(
        "user_watchlist_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("watchlist_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("symbol", sa.String(length=10), nullable=False),
        sa.Column("exchange", sa.String(length=32), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["user_watchlists.id"],
            name="fk_watchlist_items_watchlist_id",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("watchlist_id", "symbol", name="uq_watchlist_item_symbol"),
    )
    op.create_index(
        "idx_watchlist_items_watchlist_id",
        "user_watchlist_items",
        ["watchlist_id"],
    )
    op.create_index("idx_watchlist_items_symbol", "user_watchlist_items", ["symbol"])

    # 4. Update tasks table with requested_by
    op.add_column(
        "tasks",
        sa.Column("requested_by", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("idx_tasks_requested_by", "tasks", ["requested_by"])


def downgrade() -> None:
    op.drop_index("idx_tasks_requested_by", table_name="tasks")
    op.drop_column("tasks", "requested_by")

    op.drop_index("idx_watchlist_items_symbol", table_name="user_watchlist_items")
    op.drop_index("idx_watchlist_items_watchlist_id", table_name="user_watchlist_items")
    op.drop_table("user_watchlist_items")

    op.drop_index("idx_user_watchlists_user_id", table_name="user_watchlists")
    op.drop_table("user_watchlists")

    op.drop_index("idx_user_tag_user_id", table_name="stock_user_tags")
    op.drop_constraint("uq_stock_user_tag", "stock_user_tags", type_="unique")
    op.create_unique_constraint(
        "uq_stock_user_tag",
        "stock_user_tags",
        ["symbol", "tag_name"],
    )
    op.drop_column("stock_user_tags", "user_id")
