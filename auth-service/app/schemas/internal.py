"""Internal microservice API schemas for API Gateway and downstream services."""

import uuid
from typing import Any

from pydantic import BaseModel, Field


class SessionIntrospectRequest(BaseModel):
    """Session introspection payload received from API Gateway."""

    session_id: str = Field(..., description="Opaque session identifier from Cookie")
    csrf_token: str | None = Field(
        default=None,
        description="CSRF token supplied in X-CSRF-Token header",
    )


class SessionIntrospectResponse(BaseModel):
    """Session introspection result."""

    active: bool
    user_id: uuid.UUID | None = None
    username: str | None = None
    roles: list[str] = Field(default_factory=list)
    permissions: list[str] = Field(default_factory=list)
    csrf_valid: bool | None = None
    expires_in: int | None = None


class PrincipalAssertionRequest(BaseModel):
    """Request to sign a short-lived Principal Assertion JWT."""

    session_id: str = Field(..., description="Active session ID")
    ttl: int | None = Field(default=60, description="Assertion TTL in seconds (default 60s)")
    custom_claims: dict[str, Any] | None = Field(
        default=None,
        description="Optional custom claims",
    )


class PrincipalAssertionResponse(BaseModel):
    """Principal Assertion response."""

    assertion_token: str
    token_type: str = "Bearer"
    expires_in: int
    kid: str
    algorithm: str
