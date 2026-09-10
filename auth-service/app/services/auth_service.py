"""User authentication, registration, and profile service."""

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from redis.asyncio import Redis
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.crypto import hash_password, hash_token, verify_and_update_password
from app.models.rbac import AuthRolePermission, AuthUserRole
from app.models.user import AuthCredential, AuthUser
from app.schemas.auth import UserLoginRequest, UserOut, UserRegisterRequest
from app.services.audit_service import record_audit_event
from app.services.session_service import SessionService


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
        # 1. Fetch user roles
        role_stmt = select(AuthUserRole.role_id).where(AuthUserRole.user_id == user_id)
        role_res = await db.execute(role_stmt)
        roles = list(role_res.scalars().all())

        if not roles:
            return [], []

        # 2. Fetch associated permissions
        perm_stmt = select(AuthRolePermission.permission_id).where(
            AuthRolePermission.role_id.in_(roles),
        )
        perm_res = await db.execute(perm_stmt)
        permissions = sorted(list(set(perm_res.scalars().all())))

        return roles, permissions

    async def get_user_profile(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> UserOut:
        """Fetch complete user profile with roles and permissions."""
        stmt = select(AuthUser).where(AuthUser.id == user_id)
        res = await db.execute(stmt)
        user = res.scalar_one_or_none()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"code": "RESOURCE_NOT_FOUND", "message": "用户不存在"},
            )

        roles, permissions = await self.get_user_roles_and_permissions(db, user.id)
        return UserOut(
            id=user.id,
            username=user.username,
            email=user.email,
            display_name=user.display_name,
            status=user.status,
            is_superuser=user.is_superuser,
            roles=roles,
            permissions=permissions,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )

    async def register_user(
        self,
        db: AsyncSession,
        req: UserRegisterRequest,
        ip_address: str | None = None,
        user_agent: str | None = None,
        trace_id: str | None = None,
        default_role: str = "trader",
    ) -> UserOut:
        """Register a new user account."""
        # 1. Check duplicate username or email
        dup_stmt = select(AuthUser).where(
            or_(
                func.lower(AuthUser.username) == func.lower(req.username),
                func.lower(AuthUser.email) == func.lower(req.email),
            ),
        )
        dup_res = await db.execute(dup_stmt)
        existing = dup_res.scalar_one_or_none()
        if existing:
            if existing.username.lower() == req.username.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": "RESOURCE_ALREADY_EXISTS", "message": "该用户名已被注册"},
                )
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "RESOURCE_ALREADY_EXISTS", "message": "该邮箱已被注册"},
            )

        # 2. Hash password
        pwd_hash = hash_password(req.password)

        # 3. Create user entity
        user = AuthUser(
            id=uuid.uuid4(),
            username=req.username,
            email=req.email,
            display_name=req.display_name or req.username,
            status="active",
            is_superuser=False,
            failed_attempts=0,
        )
        db.add(user)
        await db.flush()

        # 4. Create password credential
        credential = AuthCredential(
            id=uuid.uuid4(),
            user_id=user.id,
            auth_type="password",
            password_hash=pwd_hash,
        )
        db.add(credential)

        # 5. Assign default role
        user_role = AuthUserRole(
            user_id=user.id,
            role_id=default_role,
        )
        db.add(user_role)
        await db.commit()

        # 6. Audit log
        await record_audit_event(
            db,
            event_type="auth.register.success",
            status="SUCCESS",
            user_id=user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            trace_id=trace_id,
            payload={"username": user.username, "email": user.email, "role": default_role},
        )

        return await self.get_user_profile(db, user.id)

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

        # 1. Lookup user
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

        if not user:
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

        # 2. Check lock status
        if user.status == "locked":
            if user.locked_until and user.locked_until > now_dt:
                await record_audit_event(
                    db,
                    event_type="auth.login.locked",
                    status="DENIED",
                    user_id=user.id,
                    ip_address=ip_address,
                    user_agent=user_agent,
                    trace_id=trace_id,
                    payload={"locked_until": str(user.locked_until)},
                )
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "code": "AUTH_INVALID_CREDENTIALS",
                        "message": "账户已因连续错误被临时锁定，请稍后重试",
                    },
                )
            else:
                # Unlock
                user.status = "active"
                user.failed_attempts = 0
                user.locked_until = None

        if user.status != "active":
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "AUTH_FORBIDDEN", "message": "账户状态异常，无法登录"},
            )

        # 3. Locate password credential
        pwd_credential = next(
            (c for c in user.credentials if c.auth_type == "password"),
            None,
        )
        if not pwd_credential or not pwd_credential.password_hash:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"code": "AUTH_INVALID_CREDENTIALS", "message": "未设置密码认证凭证"},
            )

        # 4. Verify password with Argon2id
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

        # 5. Success path: update password if rehashed
        if rehash:
            pwd_credential.password_hash = rehash
            pwd_credential.password_updated_at = now_dt

        user.failed_attempts = 0
        user.locked_until = None
        user.last_login_at = now_dt
        await db.commit()

        # 6. Fetch roles and permissions
        roles, permissions = await self.get_user_roles_and_permissions(db, user.id)

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
            roles=roles,
            permissions=permissions,
            last_login_at=user.last_login_at,
            created_at=user.created_at,
        )

        return user_out, session_id, csrf_token, expires_in
