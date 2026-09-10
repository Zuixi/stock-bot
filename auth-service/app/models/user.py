"""User and credential database models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.rbac import AuthUserRole
    from app.models.session import AuthSession


class AuthUser(Base):
    """User account core model."""

    __tablename__ = "auth_users"
    __table_args__ = (
        UniqueConstraint("username", name="uq_auth_users_username"),
        UniqueConstraint("email", name="uq_auth_users_email"),
        CheckConstraint(
            "status IN ('active', 'suspended', 'locked', 'pending_verification')",
            name="chk_auth_user_status",
        ),
        Index("idx_auth_users_status", "status"),
        Index("idx_auth_users_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="active", nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    credentials: Mapped[list[AuthCredential]] = relationship(
        "AuthCredential",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    user_roles: Mapped[list[AuthUserRole]] = relationship(
        "AuthUserRole",
        back_populates="user",
        foreign_keys="[AuthUserRole.user_id]",
        cascade="all, delete-orphan",
    )
    sessions: Mapped[list[AuthSession]] = relationship(
        "AuthSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )


class AuthCredential(Base):
    """User credential model (isolates password hash from main user entity)."""

    __tablename__ = "auth_credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "auth_type", name="uq_auth_credentials_user_type"),
        Index("idx_auth_credentials_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        nullable=False,
    )
    auth_type: Mapped[str] = mapped_column(String(32), default="password", nullable=False)
    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    salt: Mapped[str | None] = mapped_column(String(64), nullable=True)
    password_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped[AuthUser] = relationship("AuthUser", back_populates="credentials")
