"""add decision journal tables

Revision ID: c64df59c0e86
Revises: b6a1f0c2d7e4
Create Date: 2026-10-18 12:16:13.364942

spec §S9.2：决策日志**只新增 2 张表**（`decision_journal_entries` +
`decision_journal_reviews`），DDL 逐字照 spec（含 4 条 check、1 条 unique、2 个索引）。

三条不可越界的点：

1. **append-only**：`reviews` 只有 INSERT，`unique(entry_id)` 让"二次复盘"报冲突
   而不是静默覆盖 —— 不复盘就无法把"错在哪"洗掉；
2. **快照固化在行内**：`snapshot` jsonb 创建时写入，之后不随行情变化（重跑不改）；
3. **到期日固化**：`due_on` = `created_at::date + horizon_days`，写入时算好，便于
   `(user_id, due_on)` 索引取"待复盘"，而不是每次现算。

不建持仓/成本/盈亏表，不做软删除（spec §S9.2「不做什么」）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "c64df59c0e86"
down_revision: str | None = "b6a1f0c2d7e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "decision_journal_entries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("user_id", UUID(as_uuid=True), nullable=False),
        # NULL = 市场级判断（没有标的）
        sa.Column("symbol", sa.String(length=16), nullable=True),
        sa.Column("kind", sa.String(length=16), server_default="watch", nullable=False),
        sa.Column("thesis", sa.Text(), nullable=False),
        # {"kind":"price_below","value":38.0} | {"kind":"price_above",...}
        # | {"kind":"date_only","value":"2026-12-31"}
        sa.Column("invalidation", JSONB(), nullable=False),
        sa.Column("horizon_days", sa.Integer(), nullable=False),
        # 创建时的市场快照（spec §S9.3），不可变
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # created_at::date + horizon_days（写入时固化，便于索引）
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("char_length(thesis) between 1 and 200", name="ck_journal_thesis_len"),
        sa.CheckConstraint("horizon_days in (7, 30, 90)", name="ck_journal_horizon"),
        sa.CheckConstraint(
            "invalidation ? 'kind' and invalidation ? 'value'",
            name="ck_journal_invalidation_keys",
        ),
        sa.CheckConstraint("kind in ('watch','buy','sell','pass','hold')", name="ck_journal_kind"),
    )
    op.create_index(
        "idx_journal_entries_user_due",
        "decision_journal_entries",
        ["user_id", "due_on"],
        unique=False,
    )
    op.create_index(
        "idx_journal_entries_user_created",
        "decision_journal_entries",
        ["user_id", sa.text("created_at DESC")],
        unique=False,
    )

    # ★ append-only：只有 INSERT；一次复盘一条，不可改
    op.create_table(
        "decision_journal_reviews",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("entry_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # 绝对收益 %
        sa.Column("outcome_pct", sa.Numeric(precision=10, scale=4), nullable=False),
        # 基准（沪深300 或所属申万一级）%
        sa.Column("bench_pct", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("excess_pct", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("invalidation_hit", sa.Boolean(), nullable=False),
        sa.Column("verdict", sa.String(length=24), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["entry_id"], ["decision_journal_entries.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entry_id", name="uq_journal_review_entry"),
        sa.CheckConstraint(
            "verdict in ('thesis_wrong','timing_wrong','discipline_break',"
            "'right_for_wrong_reason','luck')",
            name="ck_journal_verdict",
        ),
    )


def downgrade() -> None:
    op.drop_table("decision_journal_reviews")
    op.drop_index("idx_journal_entries_user_created", table_name="decision_journal_entries")
    op.drop_index("idx_journal_entries_user_due", table_name="decision_journal_entries")
    op.drop_table("decision_journal_entries")
