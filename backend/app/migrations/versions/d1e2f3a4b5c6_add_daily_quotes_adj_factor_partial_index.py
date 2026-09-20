"""add partial index for daily_quotes adj_factor gap lookup

Revision ID: d1e2f3a4b5c6
Revises: c9e3f2d4a5b6
Create Date: 2026-09-17

``quote_repo.list_missing_adj_factor_pairs`` 的对账路径要回答"哪些股票**曾经**有
``adj_factor``"，其子查询是 ``SELECT DISTINCT stock_id FROM daily_quotes WHERE
adj_factor IS NOT NULL``。在 4.36M 行的 daily_quotes 上实测（2026-09-17，窗口
2026-09-01~09-16）为 **Parallel Seq Scan**：76k buffers、约 195ms，扫描 4.36M 行只为
得到 17k 行。

本迁移建 ``daily_quotes(stock_id) WHERE adj_factor IS NOT NULL`` 的部分索引：谓词与
子查询完全一致，规划器可改走 index-only scan 直接得到有序的 stock_id 列表，
不必再物化+排序全表。

与 ``c9e3f2d4a5b6`` 同一约定：daily_quotes 是大表，用 ``CONCURRENTLY`` 避免
ACCESS EXCLUSIVE 锁；``CONCURRENTLY`` 不能在事务内，故显式 ``autocommit_block``。
downgrade 对称地并发删除。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d1e2f3a4b5c6"
down_revision: str | None = "c9e3f2d4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "idx_daily_quotes_stock_id_adj_factor"


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.create_index(
            _INDEX_NAME,
            "daily_quotes",
            ["stock_id"],
            unique=False,
            postgresql_where=sa.text("adj_factor IS NOT NULL"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX_NAME,
            table_name="daily_quotes",
            postgresql_concurrently=True,
        )
