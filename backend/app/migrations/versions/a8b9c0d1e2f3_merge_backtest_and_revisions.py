"""merge backtest + report-revision heads

Revision ID: a8b9c0d1e2f3
Revises: d5e6f7a8b9c0, f7a8b9c0d1e2
"""

from __future__ import annotations

revision: str = "a8b9c0d1e2f3"
down_revision: tuple[str, str] = ("d5e6f7a8b9c0", "f7a8b9c0d1e2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
