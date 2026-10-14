"""Login denial matrix as pure functions (ADR 0010).

Single source of truth for "which user may log in and what to say when they
may not". Replaces scattered ``if user.status != "active"`` branches.

Deliberately NO transition table: the admin side only has active<->suspended
with one endpoint per edge, each doing its own precondition check (a 2-edge
state machine does not need a framework).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.user import AuthCredential, AuthUser

# The only status that may obtain a session. "locked" clears itself when the
# lockout window passes (handled in authenticate_user). "pending_verification"
# is a legacy enum value that nothing produces or accepts.
LOGINABLE = frozenset({"active"})
LEGACY_UNUSED = frozenset({"pending_verification"})

# HTTP 401 = "we don't tell you more", 403 = "you proved who you are, so we do".
DENIAL_LOCKED = (
    401,
    "AUTH_INVALID_CREDENTIALS",
    "账户已因连续错误被临时锁定，请稍后重试",
)
DENIAL_SUSPENDED = (403, "AUTH_ACCOUNT_SUSPENDED", "账号已被停用")
DENIAL_TEMP_PASSWORD_EXPIRED = (
    403,
    "AUTH_TEMP_PASSWORD_EXPIRED",
    "临时密码已过期，请联系管理员重新签发",
)
DENIAL_STATUS = (403, "AUTH_FORBIDDEN", "账户状态异常，无法登录")


def _password_credential(user: AuthUser) -> AuthCredential | None:
    return next((c for c in user.credentials if c.auth_type == "password"), None)


def temp_password_expired(credential: AuthCredential | None, now: datetime) -> bool:
    """True when a not-yet-replaced temp password is past its deadline."""
    if credential is None or not credential.must_change_password:
        return False
    expires = credential.temp_password_expires_at
    if expires is None:
        return False
    # SQLite stores naive datetimes; normalize before comparing.
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return expires <= now


def login_denial(user: AuthUser, now: datetime | None = None) -> tuple[int, str, str] | None:
    """Denial triple for a user whose password was ALREADY verified.

    Call order matters: verify the password first, then call this — the
    denial reason must never leak to someone who does not know the password.
    """
    now = now or datetime.now(UTC)

    if user.status == "locked":
        locked_until = user.locked_until
        if locked_until is not None:
            if locked_until.tzinfo is None:
                locked_until = locked_until.replace(tzinfo=UTC)
            now_cmp = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
            if locked_until > now_cmp:
                return DENIAL_LOCKED
        return None  # lock expired -> caller clears it and proceeds

    if user.status == "suspended":
        http_status, code, msg = DENIAL_SUSPENDED
        reason = user.status_reason or ""
        detail = f"{msg}：{reason}" if reason else msg
        return (http_status, code, detail)

    if user.status not in LOGINABLE:
        return DENIAL_STATUS

    if temp_password_expired(_password_credential(user), now):
        return DENIAL_TEMP_PASSWORD_EXPIRED

    return None
