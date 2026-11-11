"""storage tuning: autovacuum / fillfactor for upsert-heavy tables (P6)

Revision ID: c3d4e5f6a7b8
Revises: a7b8c9d0e1f2
Create Date: 2026-11-11

实测（2026-11-10）：``daily_quotes`` 4.55M 行 / 1.44GB / 485K 死元组（10.7%）+
``last_autovacuum`` 为空；``daily_basic_indicators`` 106K 行 / 698MB。默认 autovacuum
阈值（20%）对这些"小表高频 upsert"来得太晚。

本迁移只改**存储参数**，不改数据、不锁表（ALTER TABLE SET 是元数据操作）。分区切换
（``scripts/partition_daily_quotes.py``）是独立运维步骤，不放在 Alembic 里——它需要
``RENAME`` 与回滚窗口，迁移无法安全表达。
"""

from collections.abc import Sequence

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: str | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES = (
    "daily_quotes",
    "daily_basic_indicators",
    "stock_price_limits",
    "sector_moneyflow_snapshots",
)


def upgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} SET (
                autovacuum_vacuum_scale_factor = 0.02,
                autovacuum_analyze_scale_factor = 0.01,
                autovacuum_vacuum_cost_delay = 2,
                fillfactor = 90
            )
            """
        )


def downgrade() -> None:
    for table in _TABLES:
        op.execute(
            f"""
            ALTER TABLE {table} RESET (
                autovacuum_vacuum_scale_factor,
                autovacuum_analyze_scale_factor,
                autovacuum_vacuum_cost_delay,
                fillfactor
            )
            """
        )
