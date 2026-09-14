"""drop redundant daily_quotes index

Revision ID: c9e3f2d4a5b6
Revises: b8d2e1c3f4a5
Create Date: 2026-09-14

uq_daily_quotes_stock_date 与 idx_daily_quotes_stock_date 列完全相同；实测（2026-09-14）
窗口/连板查询走的是 **idx_daily_quotes_stock_date**（同列索引规划器按 OID 任选），
删掉它之后同一查询自动改走 uq_。另删 ix_daily_quotes_stock_id（与唯一约束首列重复）。
4.33M 行表每次写三个索引中两个是纯写放大。用 CONCURRENTLY 避免 ACCESS EXCLUSIVE
（DROP INDEX CONCURRENTLY 不能在事务内，故显式 autocommit_block）；本库实测 daily_quotes
为普通表（relkind='r'，非分区），CONCURRENTLY 可用。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c9e3f2d4a5b6"
down_revision: str | None = "b8d2e1c3f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "idx_daily_quotes_stock_date",
            table_name="daily_quotes",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_daily_quotes_stock_id",
            table_name="daily_quotes",
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.create_index(
        "idx_daily_quotes_stock_date", "daily_quotes", ["stock_id", "trade_date"]
    )
    op.create_index("ix_daily_quotes_stock_id", "daily_quotes", ["stock_id"])
