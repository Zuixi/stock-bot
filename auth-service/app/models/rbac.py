"""RBAC (Role-Based Access Control) database models."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.user import AuthUser


class AuthRole(Base):
    """System role definition."""

    __tablename__ = "auth_roles"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
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
    role_permissions: Mapped[list[AuthRolePermission]] = relationship(
        "AuthRolePermission",
        back_populates="role",
        cascade="all, delete-orphan",
    )
    user_roles: Mapped[list[AuthUserRole]] = relationship(
        "AuthUserRole",
        back_populates="role",
        cascade="all, delete-orphan",
    )


class AuthPermission(Base):
    """Fine-grained permission item."""

    __tablename__ = "auth_permissions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    role_permissions: Mapped[list[AuthRolePermission]] = relationship(
        "AuthRolePermission",
        back_populates="permission",
        cascade="all, delete-orphan",
    )


class AuthRolePermission(Base):
    """Association between roles and permissions."""

    __tablename__ = "auth_role_permissions"

    role_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("auth_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    permission_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("auth_permissions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    role: Mapped[AuthRole] = relationship("AuthRole", back_populates="role_permissions")
    permission: Mapped[AuthPermission] = relationship(
        "AuthPermission",
        back_populates="role_permissions",
    )


class AuthUserRole(Base):
    """Association between users and roles."""

    __tablename__ = "auth_user_roles"
    __table_args__ = (Index("idx_auth_user_roles_user", "user_id"),)

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("auth_users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    role_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("auth_roles.id", ondelete="CASCADE"),
        primary_key=True,
    )
    assigned_by: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("auth_users.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    # Relationships
    user: Mapped[AuthUser] = relationship(
        "AuthUser",
        back_populates="user_roles",
        foreign_keys=[user_id],
    )
    role: Mapped[AuthRole] = relationship("AuthRole", back_populates="user_roles")
