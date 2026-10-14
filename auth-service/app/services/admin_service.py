"""Admin user-management service (ADR 0010).

The single provisioning code path for both the admin API and the
bootstrap CLI: ``provision_user``. Two provisioning implementations means
fixing a bug in one and shipping the other.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import hash_password
from app.models.rbac import AuthRole, AuthUserRole
from app.models.user import AuthCredential, AuthUser
from app.services.audit_service import record_audit_event
from app.services.auth_service import ensure_unique_identity, load_user
from app.services.session_service import SessionService


def _generate_temp_password() -> str:
    # 16 url-safe chars, satisfies the 8-128 policy without admin-chosen
    # weak passwords.
    return secrets.token_urlsafe(12)


async def _ensure_roles_exist(db: AsyncSession, role_ids: list[str]) -> None:
    """Trust-boundary validation: unknown role_id -> 404, not a silent no-op."""
    found = (await db.execute(select(AuthRole.id).where(AuthRole.id.in_(role_ids)))).scalars().all()
    missing = set(role_ids) - set(found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "RESOURCE_NOT_FOUND",
                "message": f"角色不存在: {', '.join(sorted(missing))}",
            },
        )


class AdminService:
    def __init__(self, session_service: SessionService) -> None:
        self.session_service = session_service

    # ── queries ────────────────────────────────────────────────────────────

    async def list_users(
        self,
        db: AsyncSession,
        *,
        status_filter: str | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[AuthUser], int]:
        cond = []
        if status_filter:
            cond.append(AuthUser.status == status_filter)
        if q:
            like = f"%{q.lower()}%"
            cond.append(
                or_(
                    func.lower(AuthUser.username).like(like),
                    func.lower(AuthUser.email).like(like),
                )
            )
        base = select(AuthUser)
        count_stmt = select(func.count()).select_from(AuthUser)
        if cond:
            base = base.where(*cond)
            count_stmt = count_stmt.where(*cond)
        total = (await db.execute(count_stmt)).scalar_one()
        res = await db.execute(
            base.order_by(AuthUser.created_at.desc()).offset(offset).limit(limit)
        )
        return list(res.scalars().all()), total

    # ── provisioning (single code path, also used by the CLI) ──────────────

    async def provision_user(
        self,
        db: AsyncSession,
        *,
        username: str,
        email: str,
        display_name: str | None,
        roles: list[str],
        acting_user: AuthUser | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[AuthUser, str, datetime]:
        """Create an active user with roles and a one-time temp password.

        Returns (user, temp_password, temp_password_expires_at). The password
        exists in memory and in the HTTP response once — never in a payload,
        log, or extra column.
        """
        if not roles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "开户必须指定至少一个角色"},
            )
        await ensure_unique_identity(db, username, email)
        await _ensure_roles_exist(db, roles)

        temp_password = _generate_temp_password()
        expires_at = datetime.now(UTC) + timedelta(hours=settings.temp_password_ttl_hours)

        user = AuthUser(
            id=uuid.uuid4(),
            username=username,
            email=email,
            display_name=display_name or username,
            status="active",
            is_superuser=False,
            failed_attempts=0,
        )
        db.add(user)
        await db.flush()

        db.add(
            AuthCredential(
                id=uuid.uuid4(),
                user_id=user.id,
                auth_type="password",
                password_hash=hash_password(temp_password),
                must_change_password=True,
                temp_password_expires_at=expires_at,
            )
        )
        for role_id in roles:
            db.add(
                AuthUserRole(
                    user_id=user.id,
                    role_id=role_id,
                    assigned_by=acting_user.id if acting_user else None,
                )
            )
        await db.commit()

        await record_audit_event(
            db,
            event_type="auth.user.provisioned",
            status="SUCCESS",
            user_id=acting_user.id if acting_user else user.id,
            ip_address=ip_address,
            user_agent=user_agent,
            trace_id=trace_id,
            payload={
                "target_user_id": str(user.id),
                "username": user.username,
                "roles": roles,
                "temp_password_expires_at": expires_at.isoformat(),
            },
        )
        return user, temp_password, expires_at

    # ── lifecycle ──────────────────────────────────────────────────────────

    def _assert_not_self(self, acting: AuthUser, target: AuthUser) -> None:
        if acting.id == target.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "INVALID_OPERATION", "message": "不能对自己执行该操作"},
            )

    async def suspend_user(
        self,
        db: AsyncSession,
        *,
        target_id: uuid.UUID,
        acting: AuthUser,
        reason: str | None = None,
        ip_address: str | None = None,
        trace_id: str | None = None,
    ) -> AuthUser:
        target = await load_user(db, target_id)
        self._assert_not_self(acting, target)
        if target.status != "active":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "INVALID_STATE_TRANSITION",
                    "message": f"仅 active 用户可停用（当前 {target.status}）",
                },
            )
        before = target.status
        target.status = "suspended"
        target.status_reason = reason
        await db.commit()
        await self.session_service.revoke_all_user_sessions(db, target.id)
        await record_audit_event(
            db,
            event_type="auth.user.suspended",
            status="SUCCESS",
            user_id=acting.id,
            ip_address=ip_address,
            trace_id=trace_id,
            payload={"target_user_id": str(target.id), "before": before, "reason": reason},
        )
        return target

    async def reactivate_user(
        self,
        db: AsyncSession,
        *,
        target_id: uuid.UUID,
        acting: AuthUser,
        ip_address: str | None = None,
        trace_id: str | None = None,
    ) -> AuthUser:
        target = await load_user(db, target_id)
        if target.status != "suspended":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "INVALID_STATE_TRANSITION",
                    "message": f"仅 suspended 用户可重新启用（当前 {target.status}）",
                },
            )
        target.status = "active"
        target.status_reason = None
        target.failed_attempts = 0
        target.locked_until = None
        await db.commit()
        await record_audit_event(
            db,
            event_type="auth.user.reactivated",
            status="SUCCESS",
            user_id=acting.id,
            ip_address=ip_address,
            trace_id=trace_id,
            payload={"target_user_id": str(target.id)},
        )
        return target

    async def replace_roles(
        self,
        db: AsyncSession,
        *,
        target_id: uuid.UUID,
        roles: list[str],
        acting: AuthUser,
        ip_address: str | None = None,
        trace_id: str | None = None,
    ) -> AuthUser:
        target = await load_user(db, target_id)
        self._assert_not_self(acting, target)
        if not roles:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"code": "VALIDATION_ERROR", "message": "至少保留一个角色"},
            )
        await _ensure_roles_exist(db, roles)

        res = await db.execute(select(AuthUserRole).where(AuthUserRole.user_id == target.id))
        existing = list(res.scalars().all())
        before = sorted(ur.role_id for ur in existing)

        current_ids = {ur.role_id for ur in existing}
        wanted = set(roles)
        for ur in existing:
            if ur.role_id not in wanted:
                await db.delete(ur)
        for role_id in wanted - current_ids:
            db.add(AuthUserRole(user_id=target.id, role_id=role_id, assigned_by=acting.id))
        await db.commit()

        # Role change alters what the user can do: kick their sessions so the
        # next login (and forward-auth within 25s) reflects the new grants.
        await self.session_service.revoke_all_user_sessions(db, target.id)
        await record_audit_event(
            db,
            event_type="auth.user.roles.changed",
            status="SUCCESS",
            user_id=acting.id,
            ip_address=ip_address,
            trace_id=trace_id,
            payload={"target_user_id": str(target.id), "before": before, "after": sorted(roles)},
        )
        return target

    async def reset_password(
        self,
        db: AsyncSession,
        *,
        target_id: uuid.UUID,
        acting: AuthUser,
        ip_address: str | None = None,
        trace_id: str | None = None,
    ) -> tuple[AuthUser, str, datetime]:
        """Issue a fresh temp password; revoke every existing session."""
        target = await load_user(db, target_id)
        temp_password = _generate_temp_password()
        expires_at = datetime.now(UTC) + timedelta(hours=settings.temp_password_ttl_hours)

        res = await db.execute(
            select(AuthCredential).where(
                AuthCredential.user_id == target.id,
                AuthCredential.auth_type == "password",
            )
        )
        credential = res.scalar_one_or_none()
        if credential is None:
            credential = AuthCredential(id=uuid.uuid4(), user_id=target.id, auth_type="password")
            db.add(credential)
        credential.password_hash = hash_password(temp_password)
        credential.password_updated_at = datetime.now(UTC)
        credential.must_change_password = True
        credential.temp_password_expires_at = expires_at
        await db.commit()

        await self.session_service.revoke_all_user_sessions(db, target.id)
        await record_audit_event(
            db,
            event_type="auth.user.password.reset",
            status="SUCCESS",
            user_id=acting.id,
            ip_address=ip_address,
            trace_id=trace_id,
            payload={
                "target_user_id": str(target.id),
                "temp_password_expires_at": expires_at.isoformat(),
            },
        )
        return target, temp_password, expires_at
