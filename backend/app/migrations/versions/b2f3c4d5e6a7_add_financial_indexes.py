"""add financial read-path indexes

Revision ID: b2f3c4d5e6a7
Revises: b1f2c3d4e5a6
Create Date: 2026-09-08 18:30:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "b2f3c4d5e6a7"
down_revision: str | None = "b1f2c3d4e5a6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # financial_metrics read paths:
    #   1. per-stock metric series (stock_id, metric_key) ordered by report version
    #      -> covering index (stock_id, metric_key, report_version_id) removes sort+join cost.
    #   2. JOIN from report_version (list_metrics_series) + FK cascade delete
    #      -> plain index on report_version_id.
    op.create_index(
        "idx_financial_metric_stock_key_ver",
        "financial_metrics",
        ["stock_id", "metric_key", "report_version_id"],
    )
    op.create_index(
        "idx_financial_metric_report_version",
        "financial_metrics",
        ["report_version_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_financial_metric_report_version", table_name="financial_metrics")
    op.drop_index("idx_financial_metric_stock_key_ver", table_name="financial_metrics")
