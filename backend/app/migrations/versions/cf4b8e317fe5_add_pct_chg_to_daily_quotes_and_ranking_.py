"""add pct_chg to daily_quotes and ranking indexes

Revision ID: cf4b8e317fe5
Revises: 5a1b2c3d4e5f
Create Date: 2026-09-10 20:03:53.828474

daily_quotes is NOT partitioned (verified against pg_partitioned_table), so a
plain composite index is the correct structure here. pct_chg/pre_close are
additive and nullable: the shared application stack keeps writing rows that omit
them until its own code carries the fields.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "cf4b8e317fe5"
down_revision: str | None = "5a1b2c3d4e5f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("daily_quotes", sa.Column("pre_close", sa.Numeric(12, 4), nullable=True))
    op.add_column("daily_quotes", sa.Column("pct_chg", sa.Numeric(8, 4), nullable=True))
    # Ranking indexes: homepage reads are "latest trade_date, order by pct_chg/amount".
    op.create_index("idx_daily_quotes_date_pct", "daily_quotes", ["trade_date", "pct_chg"])
    op.create_index("idx_daily_quotes_date_amount", "daily_quotes", ["trade_date", "amount"])
    op.create_index(
        "idx_daily_basic_date_turnover",
        "daily_basic_indicators",
        ["trade_date", "turnover_rate"],
    )


def downgrade() -> None:
    op.drop_index("idx_daily_basic_date_turnover", table_name="daily_basic_indicators")
    op.drop_index("idx_daily_quotes_date_amount", table_name="daily_quotes")
    op.drop_index("idx_daily_quotes_date_pct", table_name="daily_quotes")
    op.drop_column("daily_quotes", "pct_chg")
    op.drop_column("daily_quotes", "pre_close")
