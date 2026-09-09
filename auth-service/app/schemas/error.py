"""Standard structured error response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class ErrorDetail(BaseModel):
    """Optional itemized error detail."""

    field: str | None = None
    reason: str


class ErrorResponse(BaseModel):
    """Unified error response contract."""

    code: str = Field(
        ...,
        description="Machine-readable uppercase snake_case error code",
        examples=["AUTH_UNAUTHORIZED", "VALIDATION_ERROR", "AUTH_FORBIDDEN"],
    )
    message: str = Field(
        ...,
        description="Human-readable error explanation in Chinese",
        examples=["未登录或会话已过期", "用户名或密码错误"],
    )
    details: Any = Field(
        default=None,
        description="Optional structured error details",
    )
    trace_id: str = Field(
        ...,
        description="End-to-end request tracing identifier",
        examples=["req-6e5428a2-1bf3-4f9e-a89e-2dc9f3a9e661"],
    )
