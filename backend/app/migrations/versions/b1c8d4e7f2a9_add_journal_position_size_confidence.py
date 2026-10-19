"""add journal position_size and confidence

Revision ID: b1c8d4e7f2a9
Revises: a7d3e9f1b2c4
Create Date: 2026-10-19 14:40:00.000000

spec §S9.7.1 / §S9.7.5（Phase B1）：``position_size`` 与 ``confidence`` 必须**落库为列**，
不能只塞进 ``snapshot`` jsonb —— 前者是"按风险单位解释收益"的分组维度（``by_size`` /
加权超额），后者是校准的分桶维度，两者都要能被约束与聚合。

**纯加列，不搬数据、不改既有列**：

- ``position_size`` 带 ``server_default='none'`` + ``NOT NULL``：既有行由 PG 直接回填
  ``none``（= 只观察，spec 的默认值），不写 UPDATE、不改任何已有值；
- ``confidence`` **可空、无默认值**：NULL 的语义是"当时没填"，参与校准的样本只算非空行；
  给"中等信心"的默认会把没填的人算进校准里，所以宁可留空；
- 两条 check 兜住四枚举与 1..5（越界先由 API 的 pydantic 拦成 422，DB 是最后一道闸，
  与 ``ck_journal_kind`` / ``ck_journal_bet_type`` 同一条纪律）；
- 不建索引：没有按这两列过滤的查询（``stats``/``list`` 走 ``user_id``），
  加索引只会让写入变慢；
- 不 drop / 不 alter / 不 ``op.execute``：迁移只做加法（``tests/test_journal_s97_b1.py``
  用 grep 钉死这一条）。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b1c8d4e7f2a9"
down_revision: str | None = "a7d3e9f1b2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "decision_journal_entries",
        sa.Column(
            "position_size",
            sa.String(length=16),
            server_default="none",
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_journal_position_size",
        "decision_journal_entries",
        "position_size in ('none','light','normal','heavy')",
    )
    op.add_column(
        "decision_journal_entries",
        sa.Column("confidence", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        "ck_journal_confidence",
        "decision_journal_entries",
        "confidence between 1 and 5",
    )


def downgrade() -> None:
    op.drop_constraint("ck_journal_confidence", "decision_journal_entries", type_="check")
    op.drop_column("decision_journal_entries", "confidence")
    op.drop_constraint("ck_journal_position_size", "decision_journal_entries", type_="check")
    op.drop_column("decision_journal_entries", "position_size")
