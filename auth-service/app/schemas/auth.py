"""Authentication and user schemas."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserLoginRequest(BaseModel):
    """User login payload."""

    username_or_email: str = Field(
        ...,
        min_length=1,
        max_length=255,
        description="Username or email address",
    )
    password: str = Field(..., min_length=1, max_length=128, description="User password")


class UserOut(BaseModel):
    """User profile output model."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    display_name: str | None = None
    status: str
    is_superuser: bool
    must_change_password: bool = False
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime


class UpdateProfileRequest(BaseModel):
    """Self-service profile edit. All fields optional (partial update)."""

    username: str | None = Field(
        default=None,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    email: EmailStr | None = None
    display_name: str | None = Field(default=None, max_length=64)


class ChangePasswordRequest(BaseModel):
    """Self-service password change payload."""

    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="New password (min 8 characters)",
    )


class SessionOut(BaseModel):
    """User session output model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: uuid.UUID
    ip_address: str | None = None
    user_agent: str | None = None
    is_current: bool = False
    expires_at: datetime
    last_active_at: datetime
    created_at: datetime


class AuthResponse(BaseModel):
    """Authentication success response.

    Session/CSRF credentials are intentionally NOT part of the body — they are
    delivered exclusively via HttpOnly / JS-readable Set-Cookie headers.
    """

    user: UserOut
    expires_in: int


class CsrfResponse(BaseModel):
    """CSRF token query response."""

    csrf_token: str


class RoleOut(BaseModel):
    """Role output model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str | None = None
    is_system: bool
    permissions: list[str] = Field(default_factory=list)


class PermissionOut(BaseModel):
    """Permission output model."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    module: str
    name: str
    description: str | None = None


class UserRoleUpdateRequest(BaseModel):
    """Assign or update user roles payload."""

    roles: list[str] = Field(..., description="List of role IDs to assign to the user")


class AuditEventOut(BaseModel):
    """Audit log entry output model."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    event_id: uuid.UUID
    user_id: uuid.UUID | None
    event_type: str
    status: str
    ip_address: str | None
    user_agent: str | None
    trace_id: str | None
    payload: dict[str, Any] | None
    created_at: datetime
