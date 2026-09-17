"""add sector_moneyflow_snapshots (dimension, trade_date) index

Revision ID: d7c8b9a0e1f2
Revises: d1e2f3a4b5c6
Create Date: 2026-09-17

``market_data_service.get_sector_moneyflow`` 需要"该 dimension 自己实际持有的最近
``trade_date``"（``max(trade_date) WHERE dimension = ?``），以保证 ``as_of`` 与
``items`` 同口径（某天只有 industry 行时 concept 请求必须回落到 concept 自己的最近日）。

现有索引的前缀都不可用于该查询：

- ``uq_sector_moneyflow_dim_code_date (trade_date, dimension, board_code)`` 与
  ``ix_sector_moneyflow_date_dim (trade_date, dimension)`` 都以 ``trade_date`` 打头，
  而这里没有 ``trade_date`` 的等值条件，只能整索引扫；
- 新索引 ``(dimension, trade_date)`` 让每个维度做一次有界 index scan 即可取到
  ``max(trade_date)``。

表很小（实测 1,120 行），无需 CONCURRENTLY。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "d7c8b9a0e1f2"
down_revision: str | None = "d1e2f3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = "ix_sector_moneyflow_dim_date"


def upgrade() -> None:
    op.create_index(
        _INDEX_NAME,
        "sector_moneyflow_snapshots",
        ["dimension", "trade_date"],
    )


def downgrade() -> None:
    op.drop_index(_INDEX_NAME, table_name="sector_moneyflow_snapshots")
