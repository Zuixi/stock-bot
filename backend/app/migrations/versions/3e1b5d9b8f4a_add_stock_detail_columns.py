"""add stock detail columns

Revision ID: 3e1b5d9b8f4a
Revises: f88029c9ba16
Create Date: 2026-04-12 20:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "3e1b5d9b8f4a"
down_revision: str | None = "f88029c9ba16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "stocks", sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    )
    op.add_column(
        "stocks_history",
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("stocks_history", "detail")
    op.drop_column("stocks", "detail")
