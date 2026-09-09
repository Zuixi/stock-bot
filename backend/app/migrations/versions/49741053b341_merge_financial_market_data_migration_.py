"""merge financial + market-data migration chains

Merge the two parallel Alembic chains that formed when feature branches
diverged: main (financial tables/indexes) and market-data (7 data tables).
This revision linearizes them so `alembic upgrade head` resolves to one head.

Revision ID: 49741053b341
Revises: b2f3c4d5e6a7, 9d4e7a2c8b1f
Create Date: 2026-09-09 13:58:34.560450
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "49741053b341"
down_revision: tuple[str, str] | None = ("b2f3c4d5e6a7", "9d4e7a2c8b1f")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
