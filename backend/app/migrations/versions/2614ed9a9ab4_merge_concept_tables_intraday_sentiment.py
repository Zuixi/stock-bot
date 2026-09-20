"""merge concept tables + intraday sentiment

Merge the two parallel Alembic chains on the concept-boards branch:
d5b7c9e1a3f2 (concept_boards / concept_members / concept_member_changes)
and e1f2a3b4c5d6 (market_sentiment_intraday, via d7c8b9a0e1f2). Both fork
from c9e3f2d4a5b6; this empty revision linearizes them so `alembic upgrade
head` resolves to a single head.

Revision ID: 2614ed9a9ab4
Revises: d5b7c9e1a3f2, e1f2a3b4c5d6
Create Date: 2026-09-20 23:47:40.365886
"""

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "2614ed9a9ab4"
down_revision: tuple[str, str] | None = ("d5b7c9e1a3f2", "e1f2a3b4c5d6")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
