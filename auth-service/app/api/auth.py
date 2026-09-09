"""Authentication and Session management API routes."""

import hmac
import uuid

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import generate_csrf_token, verify_csrf_token
from app.core.database import get_db
from app.core.redis import get_redis
from app.schemas.auth import (
    AuthResponse,
    CsrfResponse,
    SessionOut,
    UserLoginRequest,
    UserOut,
    UserRegisterRequest,
)
from app.services.audit_service import record_audit_event
from app.services.auth_service import AuthService
from app.services.session_service import SessionService

router = APIRouter(prefix="/auth", tags=["Auth"])


async def _require_csrf_double_submit(
    stockbot_csrf: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> None:
    """Anonymous double-submit CSRF guard (applied to register / login).

    The client must first ``GET /auth/csrf`` to receive the ``stockbot_csrf``
    cookie, then echo the token back in the ``X-CSRF-Token`` header. Both must
    be present and equal (constant-time comparison), otherwise the request is
    rejected with 403 AUTH_CSRF_FAILED.
    """
    if (
        not stockbot_csrf
        or not x_csrf_token
        or not hmac.compare_digest(stockbot_csrf, x_csrf_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH_CSRF_FAILED", "message": "CSRF 校验失败"},
        )


def _extract_trace_id(request: Request, x_request_id: str | None = None) -> str:
    """Extract or generate trace ID."""
    if x_request_id:
        return x_request_id
    state_trace = getattr(request.state, "trace_id", None)
    if isinstance(state_trace, str):
        return state_trace
    return "req-unknown"


def _extract_client_meta(request: Request) -> tuple[str | None, str | None]:
    """Extract client IP and user agent."""
    ip = request.client.host if request.client else None
    if forwarded := request.headers.get("X-Forwarded-For"):
        ip = forwarded.split(",")[0].strip()
    ua = request.headers.get("User-Agent")
    return ip, ua


def _set_auth_cookies(
    response: Response,
    session_id: str,
    csrf_token: str,
    max_age: int = 86400,
) -> None:
    """Set HttpOnly Session Cookie and JavaScript-readable CSRF Cookie."""
    # 1. stockbot_session (HttpOnly)
    response.set_cookie(
        key=settings.session_cookie_name,
        value=session_id,
        max_age=max_age,
        expires=max_age,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    # 2. stockbot_csrf (Accessible to JS for X-CSRF-Token injection)
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=max_age,
        expires=max_age,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )


def _clear_auth_cookies(response: Response) -> None:
    """Clear session and CSRF cookies."""
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        domain=settings.cookie_domain,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        domain=settings.cookie_domain,
    )


@router.post(
    "/register",
    response_model=UserOut,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    dependencies=[Depends(_require_csrf_double_submit)],
)
async def register(
    req: UserRegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> UserOut:
    """Register a new user account with default permissions."""
    ip, ua = _extract_client_meta(request)
    trace_id = _extract_trace_id(request, x_request_id)
    auth_service = AuthService(redis)

    return await auth_service.register_user(
        db=db,
        req=req,
        ip_address=ip,
        user_agent=ua,
        trace_id=trace_id,
    )


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="User login with credentials and issue session cookies",
    dependencies=[Depends(_require_csrf_double_submit)],
)
async def login(
    req: UserLoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> AuthResponse:
    """Authenticate credentials, create Redis session, and set HttpOnly session cookie.

    Session/CSRF credentials are only delivered via Set-Cookie — never in the body.
    """
    ip, ua = _extract_client_meta(request)
    trace_id = _extract_trace_id(request, x_request_id)
    auth_service = AuthService(redis)

    user_out, session_id, csrf_token, expires_in = await auth_service.authenticate_user(
        db=db,
        req=req,
        ip_address=ip,
        user_agent=ua,
        trace_id=trace_id,
    )

    _set_auth_cookies(response, session_id, csrf_token, max_age=expires_in)

    return AuthResponse(user=user_out, expires_in=expires_in)


@router.post(
    "/logout",
    summary="Logout and revoke active session",
)
async def logout(
    request: Request,
    response: Response,
    stockbot_session: str | None = Cookie(default=None),
    x_csrf_token: str | None = Header(default=None, alias="X-CSRF-Token"),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
    x_request_id: str | None = Header(default=None, alias="X-Request-Id"),
) -> dict[str, str]:
    """Revoke session and clear cookies.

    With a live session the X-CSRF-Token header must match the session-bound
    CSRF token (constant-time comparison); cookie-only logout (no valid
    session) skips the check and just clears cookies.
    """
    if stockbot_session:
        session_service = SessionService(redis)
        session_data = await session_service.get_session(stockbot_session, extend_ttl=False)
        if session_data:
            if not verify_csrf_token(session_data.get("csrf_token", ""), x_csrf_token or ""):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail={"code": "AUTH_CSRF_FAILED", "message": "CSRF 校验失败"},
                )

            user_id = None
            if session_data.get("user_id"):
                user_id = uuid.UUID(session_data["user_id"])

            await session_service.revoke_session(db, stockbot_session)

            ip, ua = _extract_client_meta(request)
            trace_id = _extract_trace_id(request, x_request_id)
            await record_audit_event(
                db,
                event_type="auth.logout",
                status="SUCCESS",
                user_id=user_id,
                ip_address=ip,
                user_agent=ua,
                trace_id=trace_id,
                payload={"session_id": stockbot_session},
            )

    _clear_auth_cookies(response)
    return {"message": "已成功退出登录"}


@router.get(
    "/session",
    response_model=UserOut,
    summary="Get current logged in user profile and permissions from session",
)
@router.get(
    "/me",
    response_model=UserOut,
    summary="Alias for /auth/session",
)
async def get_current_session_user(
    stockbot_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> UserOut:
    """Resolve current active session and fetch user profile with latest roles and permissions."""
    session_id = stockbot_session
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    session_service = SessionService(redis)
    session_data = await session_service.get_session(session_id, extend_ttl=True)
    if not session_data or not session_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    user_id = uuid.UUID(session_data["user_id"])
    auth_service = AuthService(redis)
    return await auth_service.get_user_profile(db, user_id)


@router.get(
    "/csrf",
    response_model=CsrfResponse,
    summary="Get or refresh CSRF token",
)
async def get_csrf_token(
    response: Response,
    stockbot_session: str | None = Cookie(default=None),
    redis: Redis = Depends(get_redis),
) -> CsrfResponse:
    """Retrieve or generate CSRF token for the session."""
    session_id = stockbot_session
    csrf_token: str

    if session_id:
        session_service = SessionService(redis)
        session_data = await session_service.get_session(session_id, extend_ttl=True)
        if session_data and session_data.get("csrf_token"):
            csrf_token = session_data["csrf_token"]
        else:
            csrf_token = generate_csrf_token()
    else:
        csrf_token = generate_csrf_token()

    # Set non-HttpOnly CSRF cookie
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=csrf_token,
        max_age=settings.session_ttl,
        expires=settings.session_ttl,
        path="/",
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )

    return CsrfResponse(csrf_token=csrf_token)


@router.get(
    "/sessions",
    response_model=list[SessionOut],
    summary="List active sessions for current user",
)
async def list_active_sessions(
    stockbot_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> list[SessionOut]:
    """List all active login sessions for the currently authenticated user."""
    session_id = stockbot_session
    if not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    session_service = SessionService(redis)
    session_data = await session_service.get_session(session_id, extend_ttl=True)
    if not session_data or not session_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    user_id = uuid.UUID(session_data["user_id"])
    sessions = await session_service.list_user_sessions(
        db=db,
        user_id=user_id,
        current_session_id=session_id,
    )
    return [SessionOut(**s) for s in sessions]


@router.delete(
    "/sessions/{target_session_id}",
    summary="Revoke a specific session",
)
async def revoke_session_by_id(
    target_session_id: str,
    stockbot_session: str | None = Cookie(default=None),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> dict[str, str]:
    """Revoke a specific user session (kick out device)."""
    current_session_id = stockbot_session
    if not current_session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    session_service = SessionService(redis)
    current_data = await session_service.get_session(current_session_id, extend_ttl=True)
    if not current_data or not current_data.get("user_id"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "AUTH_UNAUTHORIZED", "message": "未登录或会话已过期"},
        )

    target_data = await session_service.get_session(target_session_id, extend_ttl=False)
    if target_data and target_data.get("user_id") != current_data["user_id"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "AUTH_FORBIDDEN", "message": "无权操作其他用户的会话"},
        )

    await session_service.revoke_session(db, target_session_id)
    return {"message": "会话已成功撤销"}
