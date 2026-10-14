"""Invite-only provisioning: suspend reason + temp password lifecycle.

Revision ID: a002_invite_only
Revises: a001_initial_auth
Create Date: 2026-10-14

Adds 3 columns, no tables, no constraint changes (ADR 0010):
- auth_users.status_reason            suspend reason shown to the user on login denial
- auth_credentials.must_change_password   temp password not yet replaced
- auth_credentials.temp_password_expires_at  independent of password_updated_at
  (argon2 rehash on login resets password_updated_at, so deriving expiry from it
   would silently extend the deadline)
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a002_invite_only"
down_revision: str | None = "a001_initial_auth"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_users",
        sa.Column("status_reason", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "auth_credentials",
        sa.Column(
            "must_change_password",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.add_column(
        "auth_credentials",
        sa.Column("temp_password_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("auth_credentials", "temp_password_expires_at")
    op.drop_column("auth_credentials", "must_change_password")
    op.drop_column("auth_users", "status_reason")
