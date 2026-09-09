"""Schemas package for Auth Service."""

from app.schemas.auth import (
    AuditEventOut,
    AuthResponse,
    CsrfResponse,
    PermissionOut,
    RoleOut,
    SessionOut,
    UserLoginRequest,
    UserOut,
    UserRegisterRequest,
    UserRoleUpdateRequest,
)
from app.schemas.error import ErrorDetail, ErrorResponse
from app.schemas.internal import (
    PrincipalAssertionRequest,
    PrincipalAssertionResponse,
    SessionIntrospectRequest,
    SessionIntrospectResponse,
)

__all__ = [
    "ErrorResponse",
    "ErrorDetail",
    "UserRegisterRequest",
    "UserLoginRequest",
    "UserOut",
    "SessionOut",
    "AuthResponse",
    "CsrfResponse",
    "RoleOut",
    "PermissionOut",
    "UserRoleUpdateRequest",
    "AuditEventOut",
    "SessionIntrospectRequest",
    "SessionIntrospectResponse",
    "PrincipalAssertionRequest",
    "PrincipalAssertionResponse",
]
