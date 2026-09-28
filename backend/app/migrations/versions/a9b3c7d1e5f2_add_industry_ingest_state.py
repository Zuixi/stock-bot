"""add industry_ingest_state

Revision ID: a9b3c7d1e5f2
Revises: 2614ed9a9ab4
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "a9b3c7d1e5f2"
down_revision: str | None = "2614ed9a9ab4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "industry_ingest_state",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("industry_key", sa.String(length=32), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_period", sa.Date(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("stats", JSONB(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("industry_key", "source", name="uq_ingest_state_source"),
    )


def downgrade() -> None:
    op.drop_table("industry_ingest_state")
