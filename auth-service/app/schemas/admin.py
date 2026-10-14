"""Admin console request/response schemas (ADR 0010)."""

import uuid
from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field

T = TypeVar("T")


class PageParams(BaseModel):
    page: int = Field(default=1, ge=1, description="1-based page number")
    page_size: int = Field(default=20, ge=1, le=200)

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class UserPageParams(PageParams):
    status: str | None = Field(default=None, description="Filter by account status")
    q: str | None = Field(default=None, max_length=128, description="username/email substring")


class AuditPageParams(PageParams):
    user_id: uuid.UUID | None = None
    event_type: str | None = None


class PagedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int
    page_size: int

    @classmethod
    def build(cls, items: list[T], total: int, params: PageParams) -> "PagedResponse[T]":
        return cls(items=items, total=total, page=params.page, page_size=params.page_size)


class AdminUserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    email: str
    display_name: str | None = None
    status: str
    status_reason: str | None = None
    is_superuser: bool
    must_change_password: bool = False
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    last_login_at: datetime | None = None
    created_at: datetime


class ProvisionUserRequest(BaseModel):
    username: str = Field(
        ...,
        min_length=3,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_-]+$",
    )
    email: EmailStr
    display_name: str | None = Field(default=None, max_length=64)
    roles: list[str] = Field(..., min_length=1, description="Required non-empty role ids")


class ProvisionUserResponse(BaseModel):
    """Temp password appears exactly once, here, and nowhere else."""

    user: AdminUserOut
    initial_password: str
    expires_at: datetime


class SuspendRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=255)


class ResetPasswordResponse(BaseModel):
    user: AdminUserOut
    initial_password: str
    expires_at: datetime


class ReplaceRolesRequest(BaseModel):
    roles: list[str] = Field(..., min_length=1)


# AuditEventOut / RoleOut are re-exported so callers have one import site;
# canonical definitions live in schemas.auth.
from app.schemas.auth import AuditEventOut, RoleOut  # noqa: E402, F401

__all__ = [
    "AdminUserOut",
    "AuditEventOut",
    "AuditPageParams",
    "PageParams",
    "PagedResponse",
    "ProvisionUserRequest",
    "ProvisionUserResponse",
    "ReplaceRolesRequest",
    "ResetPasswordResponse",
    "RoleOut",
    "SuspendRequest",
    "UserPageParams",
]
