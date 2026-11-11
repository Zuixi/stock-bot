"""add financial_report_revisions (Q2 restatement history)

Revision ID: f7a8b9c0d1e2
Revises: d5e6f7a8b9c0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "f7a8b9c0d1e2"
down_revision: str | None = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_report_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("report_version_id", sa.Integer(), nullable=False),
        sa.Column("stock_id", sa.Integer(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("report_type", sa.String(length=4), nullable=False),
        sa.Column("comp_type", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("ann_date", sa.Date(), nullable=True),
        sa.Column("f_ann_date", sa.Date(), nullable=True),
        sa.Column("update_flag", sa.String(length=8), nullable=True),
        sa.Column("quality_status", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_record_id", sa.Integer(), nullable=True),
        sa.Column(
            "superseded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["report_version_id"], ["financial_report_versions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_fin_revision_version",
        "financial_report_revisions",
        ["report_version_id", "superseded_at"],
    )
    op.create_index(
        "idx_fin_revision_stock_date",
        "financial_report_revisions",
        ["stock_id", "end_date"],
    )


def downgrade() -> None:
    op.drop_index("idx_fin_revision_stock_date", table_name="financial_report_revisions")
    op.drop_index("idx_fin_revision_version", table_name="financial_report_revisions")
    op.drop_table("financial_report_revisions")
