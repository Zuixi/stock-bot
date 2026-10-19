"""add journal bet_type

Revision ID: a7d3e9f1b2c4
Revises: c64df59c0e86
Create Date: 2026-10-19 09:12:41.000000

spec §S9.7.3：``bet_type``（赌的是什么 → 基准口径）必须**落库为列**，不能只写进
``snapshot`` jsonb —— 它决定 ``bench_pct`` 怎么算，按列存才能被约束与聚合。

**纯加列，不动既有数据**：

- ``bet_type`` 带 ``server_default='direction'`` + ``NOT NULL``：既有行由 PG 直接回填
  默认值（不写 UPDATE，不改任何已有值），与 spec 的"默认 ``direction``"一致；
- 一条 check 约束兜住三枚举（越界由 API 的 pydantic 先拦成 422，DB 是最后一道闸，
  与 ``ck_journal_kind`` / ``ck_journal_verdict`` 同一条纪律）；
- 不建索引：没有任何查询按 ``bet_type`` 过滤（``stats`` 按 ``user_id`` 全量聚合），
  加索引只会让写入变慢；
- 不 drop / 不 alter / 不 ``op.execute``：迁移只做加法（``tests/test_journal_s97.py``
  用 grep 钉死这一条）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e9f1b2c4"
down_revision: str | None = "c64df59c0e86"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_journal_entries",
        sa.Column(
            "bet_type",
            sa.String(length=16),
            server_default="direction",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_journal_bet_type",
        "decision_journal_entries",
        "bet_type in ('direction','relative','market')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_journal_bet_type", "decision_journal_entries", type_="check")
    op.drop_column("decision_journal_entries", "bet_type")
