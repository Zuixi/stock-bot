"""extend sector_moneyflow_snapshot (amount / flat_count)

Revision ID: e7b1c9d4a2f3
Revises: c4d8e1a72b93
Create Date: 2026-10-12

Task 2：热门板块榜（``/market/hot-boards``）改读当日快照表后，表必须承载前端
``HotBoardItem`` 契约消费的 ``amount``（东财 ``f6`` 成交额，元）与 ``flat_count``
（``f106`` 平盘家数）。两列均 NULL 允许：上游对无成分股/停牌板块给 ``-``，历史行
（本迁移之前写入的）也没有这两个事实 —— 缺失落 NULL，**绝不填 0**（0 会被读成
"成交额 0 元 / 平盘 0 家"这一事实断言）。东财板块资金流是 A 类数据（无历史接口），
本迁移只加列，不、也无法回填历史。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e7b1c9d4a2f3"
down_revision: str | None = "c4d8e1a72b93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sector_moneyflow_snapshots", sa.Column("amount", sa.Numeric(precision=20, scale=4))
    )
    op.add_column("sector_moneyflow_snapshots", sa.Column("flat_count", sa.Integer()))


def downgrade() -> None:
    op.drop_column("sector_moneyflow_snapshots", "flat_count")
    op.drop_column("sector_moneyflow_snapshots", "amount")
