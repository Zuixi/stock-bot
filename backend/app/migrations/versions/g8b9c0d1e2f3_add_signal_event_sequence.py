"""add immutable signal event sequence

Revision ID: g8b9c0d1e2f3
Revises: f7a8b9c0d1e2
Create Date: 2026-09-03 20:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "g8b9c0d1e2f3"
down_revision: str | None = "f7a8b9c0d1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "industry_signal_events",
        sa.Column("event_sequence", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT id,
                   row_number() OVER (
                       PARTITION BY industry_key, event_date
                       ORDER BY id
                   ) AS sequence
            FROM industry_signal_events
        )
        UPDATE industry_signal_events AS events
        SET event_sequence = ranked.sequence
        FROM ranked
        WHERE events.id = ranked.id
        """
    )
    op.alter_column("industry_signal_events", "event_sequence", nullable=False)
    op.drop_constraint(
        "uq_industry_signal_event", "industry_signal_events", type_="unique"
    )
    op.create_unique_constraint(
        "uq_industry_signal_event",
        "industry_signal_events",
        ["industry_key", "event_date", "event_sequence"],
    )


def downgrade() -> None:
    connection = op.get_bind()
    duplicate = connection.execute(
        sa.text(
            """
            SELECT industry_key, event_date, signal_type, phase, count(*) AS row_count
            FROM industry_signal_events
            GROUP BY industry_key, event_date, signal_type, phase
            HAVING count(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        raise RuntimeError(
            "Cannot downgrade g8b9c0d1e2f3: immutable same-day transitions contain "
            "duplicate signal/phase states under the f7 unique identity"
        )
    op.drop_constraint(
        "uq_industry_signal_event", "industry_signal_events", type_="unique"
    )
    op.create_unique_constraint(
        "uq_industry_signal_event",
        "industry_signal_events",
        ["industry_key", "event_date", "signal_type", "phase"],
    )
    op.drop_column("industry_signal_events", "event_sequence")
