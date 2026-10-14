"""Session-authenticated dependencies for auth-service's own endpoints.

Unlike backend's guard (which verifies a signed Principal Assertion),
``/auth/*`` is routed by Traefik straight to this service without forward-auth,
so authentication here = opaque session cookie -> Redis -> DB.

Roles/permissions are resolved from the DATABASE on every call, not from the
session snapshot: a demoted or suspended admin loses access on their next
request instead of when their session expires.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated, Any

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.core.database import get_db
from app.core.redis import get_redis
from app.core.user_status import LOGINABLE
from app.models.user import AuthUser
from app.services.auth_service import get_role_permission_pairs
from app.services.session_service import SessionService

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]

# Routes a must_change_password session may still touch (ADR 0010 gate,
# segment 2). NOTE: today only /auth/sessions and DELETE /auth/sessions/{id}
# actually route through get_session_user, so this list is defensive — the
# other /auth/* routes resolve their own session inline. Keep the entries
# accurate anyway: the moment one of them is switched to get_session_user,
# this list is what keeps the forced-change page reachable.
PASSWORD_CHANGE_ALLOWLIST = frozenset(
    {
        "/auth/change-password",
        "/auth/logout",
        "/auth/csrf",
        "/auth/session",
        "/auth/me",
    }
)


async def get_session_user(
    request: Request,
    db: DbDep,
    redis: RedisDep,
    stockbot_session: Annotated[str | None, Cookie()] = None,
) -> AuthUser:
    """Resolve the session cookie to a live AuthUser row.

    Enforces (in order): session exists -> user exists -> user is active
    -> temp password replaced (unless the route is allowlisted).
    """
    if not stockbot_session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    session_data = await SessionService(redis).get_session(stockbot_session, extend_ttl=True)
    if not session_data or not session_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    res = await db.execute(
        select(AuthUser)
        .options(selectinload(AuthUser.credentials), selectinload(AuthUser.user_roles))
        .where(AuthUser.id == uuid.UUID(session_data["user_id"]))
    )
    user = res.scalar_one_or_none()
    if not user or user.status not in LOGINABLE:
        # Fresh status from DB: a suspended user's session may still live in
        # Redis if revocation raced or failed; this is the backstop.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    pwd_credential = next((c for c in user.credentials if c.auth_type == "password"), None)
    must_change = bool(pwd_credential and pwd_credential.must_change_password)
    if must_change and request.url.path not in PASSWORD_CHANGE_ALLOWLIST:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "AUTH_PASSWORD_CHANGE_REQUIRED",
                "message": "首次登录需修改临时密码",
            },
        )
    return user


SessionUserDep = Annotated[AuthUser, Depends(get_session_user)]


async def resolve_user_permissions(db: AsyncSession, user: AuthUser) -> list[str]:
    """Fresh permission codes for a user, read from DB (not session snapshot)."""
    if user.is_superuser:
        return ["*"]
    role_ids = [ur.role_id for ur in user.user_roles]
    return sorted({p for _, p in await get_role_permission_pairs(db, role_ids)})


def extract_client_meta(request: Request) -> tuple[str | None, str | None]:
    """Client IP + user agent for audit trails.

    X-Forwarded-For is only honored when ``settings.trust_forwarded_for`` is
    on (behind a trusted proxy); otherwise a client-supplied header would be
    spoofable and poison the audit log.
    """
    ip = request.client.host if request.client else None
    if settings.trust_forwarded_for:
        if forwarded := request.headers.get("X-Forwarded-For"):
            ip = forwarded.split(",")[0].strip()
    return ip, request.headers.get("User-Agent")


def require_permission(*permissions: str) -> Any:
    """Factory: enforce that the session user holds ANY of the permissions."""

    async def _guard(user: SessionUserDep, db: DbDep) -> AuthUser:
        if user.is_superuser:
            return user
        granted = await resolve_user_permissions(db, user)
        if not any(p in granted for p in permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_FORBIDDEN", "message": "权限不足"},
            )
        return user

    return Depends(_guard)


async def verify_session_csrf(
    request: Request,
    redis: RedisDep,
    stockbot_session: Annotated[str | None, Cookie()] = None,
    x_csrf_token: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
) -> None:
    """Session-bound CSRF check for state-changing auth-service endpoints.

    Mirrors the logout guard: the header must constant-time-match the token
    bound to the caller's session.
    """
    if not stockbot_session:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH_CSRF_FAILED", "message": "CSRF 校验失败"},
        )
    session_data = await SessionService(redis).get_session(stockbot_session, extend_ttl=False)
    bound = (session_data or {}).get("csrf_token", "")
    if not bound or not x_csrf_token or not hmac.compare_digest(bound, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH_CSRF_FAILED", "message": "CSRF 校验失败"},
        )
