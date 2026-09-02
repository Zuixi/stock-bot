"""add fund_etf_daily / cb_daily tables

Revision ID: d5a6b7c8d9e0
Revises: c9d0e1f2a3b4
Create Date: 2026-09-03 12:00:00.000000

P5 行情面：行业关联 ETF（TuShare fund_daily）与可转债（cb_daily）日线表。
逐 (ts_code, trade_date) 幂等 upsert，代码清单由 industry registry 下发。
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5a6b7c8d9e0"
down_revision: Union[str, None] = "c9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DAILY_COLUMNS = [
    sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
    sa.Column("ts_code", sa.String(length=12), nullable=False),
    sa.Column("trade_date", sa.Date(), nullable=False),
    sa.Column("open", sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column("high", sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column("low", sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column("close", sa.Numeric(precision=12, scale=4), nullable=False),
    sa.Column("pre_close", sa.Numeric(precision=12, scale=4), nullable=True),
    sa.Column("volume", sa.Numeric(precision=20, scale=2), nullable=True),
    sa.Column("amount", sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.PrimaryKeyConstraint("id"),
]


def _create_daily_table(table: str, uq_name: str, idx_name: str) -> None:
    op.create_table(
        table,
        *_DAILY_COLUMNS,
        sa.UniqueConstraint("ts_code", "trade_date", name=uq_name),
    )
    op.create_index(idx_name, table, ["ts_code", "trade_date"], unique=False)


def upgrade() -> None:
    _create_daily_table(
        "fund_etf_daily", "uq_fund_etf_daily_code_date", "idx_fund_etf_daily_code_date"
    )
    _create_daily_table(
        "cb_daily", "uq_cb_daily_code_date", "idx_cb_daily_code_date"
    )


def downgrade() -> None:
    op.drop_index("idx_cb_daily_code_date", table_name="cb_daily")
    op.drop_table("cb_daily")
    op.drop_index("idx_fund_etf_daily_code_date", table_name="fund_etf_daily")
    op.drop_table("fund_etf_daily")
