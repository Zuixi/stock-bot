"""add tushare stock fields

Revision ID: b7c4e2a91d3f
Revises: 3e1b5d9b8f4a
Create Date: 2026-04-17 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4e2a91d3f"
down_revision: str | None = "3e1b5d9b8f4a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_COLS = [
    ("area", sa.String(), True),
    ("industry", sa.String(), True),
    ("enname", sa.String(), True),
    ("cnspell", sa.String(), True),
    ("market", sa.String(), True),
    ("curr_type", sa.String(), True),
    ("list_status", sa.String(), True),
    ("delist_date", sa.Date(), True),
    ("is_hs", sa.String(), True),
    ("act_name", sa.String(), True),
    ("act_ent_type", sa.String(), True),
]


def upgrade() -> None:
    for col_name, col_type, nullable in _NEW_COLS:
        op.add_column("stocks", sa.Column(col_name, col_type, nullable=nullable))  # type: ignore[arg-type]
        op.add_column("stocks_history", sa.Column(col_name, col_type, nullable=nullable))  # type: ignore[arg-type]


def downgrade() -> None:
    for col_name, _, _ in reversed(_NEW_COLS):
        op.drop_column("stocks_history", col_name)
        op.drop_column("stocks", col_name)
