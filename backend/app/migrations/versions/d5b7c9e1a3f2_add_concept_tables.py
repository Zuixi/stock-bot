"""add concept boards / members / member changes

Revision ID: d5b7c9e1a3f2
Revises: c9e3f2d4a5b6
Create Date: 2026-09-18

概念板块成分追踪：东财板块名录（concept_boards）、当前成分（concept_members）、
每日差分（concept_member_changes，追加式永不更新，历史从启用之日积累）。

建键说明：业务键取 symbol 而非 stock_id——stocks 名录滞后（实测冻结 2026-05-08，
66 只新上市股票缺行），以 stock_id 为键会静默丢成分股，故 stock_id 降级为可空解析列。
三张表均为裸 stock_id/symbol 追加表，不建外键（同 stock_price_limits 口径）。
不额外建 board_code 单列索引：uq_concept_member_board_symbol 的首列已服务前缀查询。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d5b7c9e1a3f2"
down_revision: str | None = "c9e3f2d4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "concept_boards",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("board_code", sa.String(length=16), nullable=False),
        sa.Column("board_name", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_code", name="uq_concept_boards_code"),
    )

    op.create_table(
        "concept_members",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("board_code", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=12), nullable=False),
        sa.Column("stock_name", sa.String(length=32), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=True),
        sa.Column("first_seen_on", sa.Date(), nullable=False),
        sa.Column("last_seen_on", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("board_code", "symbol", name="uq_concept_member_board_symbol"),
    )
    op.create_index("idx_concept_members_symbol", "concept_members", ["symbol"])
    op.create_index("idx_concept_members_stock_id", "concept_members", ["stock_id"])

    op.create_table(
        "concept_member_changes",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("observed_on", sa.Date(), nullable=False),
        sa.Column("board_code", sa.String(length=16), nullable=False),
        sa.Column("symbol", sa.String(length=12), nullable=False),
        sa.Column("stock_name", sa.String(length=32), nullable=True),
        sa.Column("change_type", sa.String(length=8), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "observed_on", "board_code", "symbol", "change_type", name="uq_concept_change_row"
        ),
    )
    op.create_index(
        "idx_concept_changes_board_date", "concept_member_changes", ["board_code", "observed_on"]
    )


def downgrade() -> None:
    op.drop_index("idx_concept_changes_board_date", table_name="concept_member_changes")
    op.drop_table("concept_member_changes")
    op.drop_index("idx_concept_members_stock_id", table_name="concept_members")
    op.drop_index("idx_concept_members_symbol", table_name="concept_members")
    op.drop_table("concept_members")
    op.drop_table("concept_boards")
