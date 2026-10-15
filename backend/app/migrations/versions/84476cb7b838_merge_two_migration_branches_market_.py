"""merge two migration branches (market hotspot x research platform ia)

Revision ID: 84476cb7b838
Revises: a3f8c2d6e9b1, c5d6e7f8a9b0
Create Date: 2026-10-12 21:59:17.410333

"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "84476cb7b838"
down_revision: str | Sequence[str] | None = ("a3f8c2d6e9b1", "c5d6e7f8a9b0")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
