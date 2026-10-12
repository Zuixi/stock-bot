"""add sector_moneyflow capture_kind

Revision ID: a3f8c2d6e9b1
Revises: e7b1c9d4a2f3
Create Date: 2026-10-12

Task 2 / Ruling 11：``sector_moneyflow_snapshots`` 需要**行级产地**才能区分"盘中 5 分钟
轮询的 f62 Top-100 子集"与"15:05 收盘后的分页全量（覆盖上游 data.total）"。``clist`` 的
``pz`` 上限是 100（实测传 1000 仍只回 100 行），所以盘中表行是"每次轮询 Top-100 的并集、
旧行永不刷新"——既不是完整当日态、也不是 ``/market/hot-boards`` 要的 f3（涨幅）全集。

``capture_kind``：``poll``（默认）| ``eod_full``。既有行全部来自盘中轮询 → 用
``server_default='poll'`` 一次性回填（``ADD COLUMN ... NOT NULL DEFAULT`` 在 PG 11+ 是
元数据操作，不重写表）。读表闸门只认 ``eod_full``，故本迁移不改变任何历史行的语义。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a3f8c2d6e9b1"
down_revision: str | None = "e7b1c9d4a2f3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "sector_moneyflow_snapshots",
        sa.Column(
            "capture_kind",
            sa.String(length=16),
            server_default=sa.text("'poll'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("sector_moneyflow_snapshots", "capture_kind")
