"""User authentication, registration, and profile service."""

import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import hash_password, hash_token, verify_and_update_password
from app.core.user_status import login_denial
from app.models.rbac import AuthRolePermission, AuthUserRole
from app.models.user import AuthUser
from app.schemas.auth import UserLoginRequest, UserOut
from app.services.audit_service import record_audit_event
from app.services.session_service import SessionService


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    """argon2id hash of an unguessable value, verified once for unknown
    usernames so login timing does not reveal account existence (ADR 0010).
    Computed lazily: hashing at import time costs every process ~64MB/30ms.
    """
    return hash_password("timing-equalizer-dummy-3f9c1a")


async def load_user(db: AsyncSession, user_id: uuid.UUID) -> AuthUser:
    """Load a user with credentials + roles eager-loaded (the single loader).

    ``populate_existing`` is deliberate: sessions run with expire_on_commit=False,
    so after a mutation (e.g. replace_roles) the identity-mapped instance keeps
    its stale relationship collections. Without this the caller reads back the
    pre-mutation roles.
    """
    res = await db.execute(
        select(AuthUser)
        .options(selectinload(AuthUser.credentials), selectinload(AuthUser.user_roles))
        .where(AuthUser.id == user_id)
        .execution_options(populate_existing=True)
    )
    user = res.scalar_one_or_none()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "RESOURCE_NOT_FOUND", "message": "用户不存在"},
        )
    return user


async def ensure_unique_identity(
    db: AsyncSession,
    username: str,
    email: str | None,
    *,
    exclude_user_id: uuid.UUID | None = None,
) -> None:
    """Raise 409 when username/email is taken by *another* account.

    ``exclude_user_id`` lets self-service profile edits keep their own values.
    """
    conds = [func.lower(AuthUser.username) == func.lower(username)]
    if email:
        conds.append(func.lower(AuthUser.email) == func.lower(email))
    stmt = select(AuthUser).where(or_(*conds))
    if exclude_user_id is not None:
        stmt = stmt.where(AuthUser.id != exclude_user_id)
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if not existing:
        return
    if existing.username.lower() == username.lower():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "RESOURCE_ALREADY_EXISTS", "message": "该用户名已被占用"},
        )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"code": "RESOURCE_ALREADY_EXISTS", "message": "该邮箱已被占用"},
    )


async def get_role_permission_pairs(
    db: AsyncSession, role_ids: set[str] | list[str]
) -> list[tuple[str, str]]:
    """(role_id, permission_id) pairs for the given roles — ONE query.

    Callers batch a whole page of users through this; per-user calls were an
    N+1 (the admin list issued one query per row).
    """
    if not role_ids:
        return []
    res = await db.execute(
        select(AuthRolePermission.role_id, AuthRolePermission.permission_id).where(
            AuthRolePermission.role_id.in_(role_ids)
        )
    )
    return [(rid, pid) for rid, pid in res.all()]


class AuthService:
    """Handles user identity, credential verification, and RBAC resolution."""

    def __init__(self, redis: Redis) -> None:
        self.session_service = SessionService(redis)

    async def get_user_roles_and_permissions(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> tuple[list[str], list[str]]:
        """Resolve role IDs and all assigned fine-grained permission codes for a user."""
        role_res = await db.execute(
            select(AuthUserRole.role_id).where(AuthUserRole.user_id == user_id)
        )
        roles = list(role_res.scalars().all())
        permissions = sorted({p for _, p in await get_role_permission_pairs(db, roles)})
        return roles, permissions

    async def update_profile(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        *,
        username: str | None = None,
        email: str | None = None,
        display_name: str | None = None,
        ip_address: str | None = None,
        trace_id: str | None = None,
    ) -> AuthUser:
        """Self-service profile edit (username / email / display name).

        No email-verification step exists, so an email change is only
        uniqueness-checked and audited — acceptable because this system has no
        email-based password reset to hijack (ADR 0010).
        """
        user = await load_user(db, user_id)
        before = {
            "username": user.username,
            "email": user.email,
            "display_name": user.display_name,
        }

        new_username = username if username is not None else user.username
        new_email = email if email is not None else user.email
        if new_username != user.username or new_email != user.email:
            await ensure_unique_identity(db, new_username, new_email, exclude_user_id=user.id)

        user.username = new_username
        user.email = new_email
        if display_name is not None:
            user.display_name = display_name or None
        await db.commit()

        await record_audit_event(
            db,
            event_type="auth.user.profile.updated",
            status="SUCCESS",
            user_id=user.id,
            ip_address=ip_address,
            trace_id=trace_id,
            payload={
                "before": before,
                "after": {
                    "username": user.username,
                    "email": user.email,
                    "display_name": user.display_name,
                },
            },
        )
        return user

    async def get_user_profile(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> UserOut:
        """Fetch complete user profile with roles and permissions."""
        user = await load_user(db, user_id)

        roles, permissions = await self.get_user_roles_and_permissions(db, user.id)
        pwd_credential = next((c for c in user.credentials if c.auth_type == "password"), None)
        return UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            is_superuser=user.is_superuser,
            must_change_password=bool(pwd_credential and pwd_credential.must_change_password),
            roles=roles,
            permissions=permissions,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )

    async def authenticate_user(
        self,
        db: AsyncSession,
        req: UserLoginRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_fingerprint: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[UserOut, str, str, int]:
        """Authenticate user credentials and create session."""
        now_dt = datetime.now(UTC)

        # 1. Lookup user (with credentials eager-loaded)
        query = (
            select(AuthUser)
            .options(selectinload(AuthUser.credentials))
            .where(
                or_(
                    func.lower(AuthUser.username) == func.lower(req.username_or_email),
                    func.lower(AuthUser.email) == func.lower(req.username_or_email),
                ),
            )
        )
        res = await db.execute(query)
        user = res.scalar_one_or_none()

        pwd_credential = (
            next((c for c in user.credentials if c.auth_type == "password"), None) if user else None
        )
        # 2. Verify password FIRST (ADR 0010: denial reasons must not leak to
        # someone who does not know the password). Unknown users — and accounts
        # with no usable password credential — burn one argon2 verification
        # against a dummy hash to equalize timing. This guard also narrows
        # `user` / `pwd_credential` for the rest of the function.
        if user is None or pwd_credential is None or not pwd_credential.password_hash:
            verify_and_update_password(req.password, _dummy_hash())
            await record_audit_event(
                db,
                event_type="auth.login.failed",
                status="FAILED",
                ip_address=ip_address,
                user_agent=user_agent,
                trace_id=trace_id,
                payload={"target": req.username_or_email, "reason": "user_not_found"},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_INVALID_CREDENTIALS", "message": "用户名或密码错误"},
            )

        is_valid, rehash = verify_and_update_password(req.password, pwd_credential.password_hash)
        if not is_valid:
            user.failed_attempts += 1
            if user.failed_attempts >= 5:
                user.status = "locked"
                user.locked_until = now_dt + timedelta(minutes=15)
            await db.commit()

            await record_audit_event(
                db,
                event_type="auth.login.failed",
                status="FAILED",
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                trace_id=trace_id,
                payload={"failed_attempts": user.failed_attempts},
            )
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_INVALID_CREDENTIALS", "message": "用户名或密码错误"},
            )

        # 3. Password proven — now evaluate status via the denial matrix.
        denial = login_denial(user, now_dt)
        if denial is not None:
            http_status, code, message = denial
            event_type = {
                "AUTH_ACCOUNT_SUSPENDED": "auth.login.suspended",
                "AUTH_TEMP_PASSWORD_EXPIRED": "auth.login.temp_password_expired",
                "AUTH_INVALID_CREDENTIALS": "auth.login.locked",
            }.get(code, "auth.login.denied")
            await record_audit_event(
                db,
                event_type=event_type,
                status="DENIED",
                user_id=user.id,
                ip_address=ip_address,
                user_agent=user_agent,
                trace_id=trace_id,
                payload={"denial_code": code},
            )
            raise HTTPException(
                status_code=http_status,
                detail={"code": code, "message": message},
            )

        # Expired lock clears itself once the password is proven correct.
        if user.status == "locked":
            user.status = "active"
            user.failed_attempts = 0
            user.locked_until = None

        # 4. Success path: update password if rehashed
        if rehash:
            pwd_credential.password_hash = rehash
            pwd_credential.password_updated_at = now_dt

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now_dt
        await db.commit()

        # 5. Fetch roles and permissions
        roles, permissions = await self.get_user_roles_and_permissions(db, user.id)

        must_change = bool(pwd_credential and pwd_credential.must_change_password)

        # 7. Create session in Redis + DB
        session_id, csrf_token, expires_in = await self.session_service.create_session(
            db=db,
            user_id=user.id,
            username=user.username,
            roles=roles,
            permissions=permissions,
            ip_address=ip_address,
            user_agent=user_agent,
            device_fingerprint=device_fingerprint,
            must_change_password=must_change,
        )

        # 8. Record audit log
        await record_audit_event(
            db,
            event_type="auth.login.success",
            status="SUCCESS",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            trace_id=trace_id,
            payload={"session_id_hash": hash_token(session_id)},
        )

        user_out = UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            is_superuser=user.is_superuser,
            must_change_password=must_change,
            roles=roles,
            permissions=permissions,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )

        return user_out, session_id, csrf_token, expires_in
