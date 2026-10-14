"""Admin console API (ADR 0010).

Every route carries its own permission dependency and every state-changing
route carries the session-bound CSRF check. ``ADMIN_ROUTES`` below mirrors
the routes for the parameterized guard tests; ``test_api_admin.py`` asserts
the two sets are equal, so a new route without a guard entry turns the
suite red instead of silently escaping coverage.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    DbDep,
    RedisDep,
    SessionUserDep,
    extract_client_meta,
    require_permission,
    verify_session_csrf,
)
from app.models.audit import AuthAuditEvent
from app.models.rbac import AuthRole
from app.models.user import AuthUser
from app.schemas.admin import (
    AdminUserOut,
    AuditEventOut,
    AuditPageParams,
    PagedResponse,
    ProvisionUserRequest,
    ProvisionUserResponse,
    ReplaceRolesRequest,
    ResetPasswordResponse,
    RoleOut,
    SuspendRequest,
    UserPageParams,
)
from app.services.admin_service import AdminService
from app.services.auth_service import get_role_permission_pairs, load_user
from app.services.session_service import SessionService

router = APIRouter(prefix="/auth/admin", tags=["Admin"])

CSRF = Depends(verify_session_csrf)

# (method, path, required permission, state-changing?) — the parameterized
# guard tests iterate this table; keep it in sync with the routes below.
ADMIN_ROUTES: list[tuple[str, str, str, bool]] = [
    ("GET", "/auth/admin/users", "users:manage", False),
    ("GET", "/auth/admin/users/{user_id}", "users:manage", False),
    ("POST", "/auth/admin/users", "users:manage", True),
    ("POST", "/auth/admin/users/{user_id}/suspend", "users:manage", True),
    ("POST", "/auth/admin/users/{user_id}/reactivate", "users:manage", True),
    ("PUT", "/auth/admin/users/{user_id}/roles", "roles:manage", True),
    ("POST", "/auth/admin/users/{user_id}/reset-password", "users:manage", True),
    ("GET", "/auth/admin/roles", "users:manage", False),
    ("GET", "/auth/admin/audit", "audit:read", False),
]


async def _permissions_by_role(db: AsyncSession, role_ids: set[str]) -> dict[str, list[str]]:
    """role_id -> permission codes for a whole page of users, in one query."""
    grouped: dict[str, list[str]] = {}
    for role_id, permission_id in await get_role_permission_pairs(db, role_ids):
        grouped.setdefault(role_id, []).append(permission_id)
    return grouped


def _user_out(user: AuthUser, permissions: list[str]) -> AdminUserOut:
    """Pure mapper — permissions arrive precomputed so callers can batch."""
    role_ids = sorted(ur.role_id for ur in user.user_roles)
    pwd = next((c for c in user.credentials if c.auth_type == "password"), None)
    return AdminUserOut(
        id=user.id,
        username=user.username,
        email=user.email,
        display_name=user.display_name,
        status=user.status,
        status_reason=user.status_reason,
        is_superuser=user.is_superuser,
        must_change_password=bool(pwd and pwd.must_change_password),
        roles=role_ids,
        permissions=permissions,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


def _merge_permissions(user: AuthUser, by_role: dict[str, list[str]]) -> list[str]:
    """Flatten a user's roles into a sorted permission list."""
    return sorted({p for ur in user.user_roles for p in by_role.get(ur.role_id, [])})


async def _detail(db: AsyncSession, user_id: uuid.UUID) -> AdminUserOut:
    """Load one user and map it — single permission query, via the shared loader."""
    user = await load_user(db, user_id)
    by_role = await _permissions_by_role(db, {ur.role_id for ur in user.user_roles})
    return _user_out(user, _merge_permissions(user, by_role))


@router.get(
    "/users",
    response_model=PagedResponse[AdminUserOut],
    summary="List users (status filter; search; badge = page_size=1 total)",
    dependencies=[require_permission("users:manage")],
)
async def list_users(
    db: DbDep,
    params: UserPageParams = Depends(),
) -> PagedResponse[AdminUserOut]:
    conds = []
    if params.status:
        conds.append(AuthUser.status == params.status)
    if params.q:
        like = f"%{params.q.lower()}%"
        conds.append(
            func.lower(AuthUser.username).like(like) | func.lower(AuthUser.email).like(like)
        )

    count_stmt = select(func.count()).select_from(AuthUser)
    list_stmt = (
        select(AuthUser)
        .options(selectinload(AuthUser.credentials), selectinload(AuthUser.user_roles))
        .order_by(AuthUser.created_at.desc())
    )
    if conds:
        count_stmt = count_stmt.where(*conds)
        list_stmt = list_stmt.where(*conds)

    total = (await db.execute(count_stmt)).scalar_one()
    users = (
        (await db.execute(list_stmt.offset(params.offset).limit(params.page_size)))
        .scalars()
        .unique()
        .all()
    )
    # One permission query for the whole page instead of one per row.
    by_role = await _permissions_by_role(db, {ur.role_id for u in users for ur in u.user_roles})
    items = [_user_out(u, _merge_permissions(u, by_role)) for u in users]
    return PagedResponse.build(items, total, params)


@router.get(
    "/users/{user_id}",
    response_model=AdminUserOut,
    summary="User detail (audit trail: /auth/admin/audit?user_id=)",
    dependencies=[require_permission("users:manage")],
)
async def get_user(user_id: uuid.UUID, db: DbDep) -> AdminUserOut:
    return await _detail(db, user_id)


@router.post(
    "/users",
    response_model=ProvisionUserResponse,
    summary="Provision a user (invite-only onboarding; roles required non-empty)",
    status_code=201,
    dependencies=[require_permission("users:manage"), CSRF],
)
async def provision_user(
    req: ProvisionUserRequest,
    request: Request,
    db: DbDep,
    redis: RedisDep,
    acting: SessionUserDep,
) -> ProvisionUserResponse:
    ip, ua = extract_client_meta(request)
    user, temp_password, expires_at = await AdminService(SessionService(redis)).provision_user(
        db,
        username=req.username,
        email=req.email,
        display_name=req.display_name,
        roles=req.roles,
        acting_user=acting,
        ip_address=ip,
        user_agent=ua,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ProvisionUserResponse(
        user=await _detail(db, user.id),
        initial_password=temp_password,
        expires_at=expires_at,
    )


@router.post(
    "/users/{user_id}/suspend",
    response_model=AdminUserOut,
    summary="Suspend an active user (revokes all sessions)",
    dependencies=[require_permission("users:manage"), CSRF],
)
async def suspend_user(
    user_id: uuid.UUID,
    req: SuspendRequest,
    request: Request,
    db: DbDep,
    redis: RedisDep,
    acting: SessionUserDep,
) -> AdminUserOut:
    ip, _ = extract_client_meta(request)
    await AdminService(SessionService(redis)).suspend_user(
        db,
        target_id=user_id,
        acting=acting,
        reason=req.reason,
        ip_address=ip,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _detail(db, user_id)


@router.post(
    "/users/{user_id}/reactivate",
    response_model=AdminUserOut,
    summary="Reactivate a suspended user",
    dependencies=[require_permission("users:manage"), CSRF],
)
async def reactivate_user(
    user_id: uuid.UUID,
    request: Request,
    db: DbDep,
    redis: RedisDep,
    acting: SessionUserDep,
) -> AdminUserOut:
    ip, _ = extract_client_meta(request)
    await AdminService(SessionService(redis)).reactivate_user(
        db,
        target_id=user_id,
        acting=acting,
        ip_address=ip,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _detail(db, user_id)


@router.put(
    "/users/{user_id}/roles",
    response_model=AdminUserOut,
    summary="Replace a user's role set (revokes all sessions)",
    dependencies=[require_permission("roles:manage"), CSRF],
)
async def replace_roles(
    user_id: uuid.UUID,
    req: ReplaceRolesRequest,
    request: Request,
    db: DbDep,
    redis: RedisDep,
    acting: SessionUserDep,
) -> AdminUserOut:
    ip, _ = extract_client_meta(request)
    await AdminService(SessionService(redis)).replace_roles(
        db,
        target_id=user_id,
        roles=req.roles,
        acting=acting,
        ip_address=ip,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return await _detail(db, user_id)


@router.post(
    "/users/{user_id}/reset-password",
    response_model=ResetPasswordResponse,
    summary="Issue a fresh temp password (revokes all sessions)",
    dependencies=[require_permission("users:manage"), CSRF],
)
async def reset_password(
    user_id: uuid.UUID,
    request: Request,
    db: DbDep,
    redis: RedisDep,
    acting: SessionUserDep,
) -> ResetPasswordResponse:
    ip, _ = extract_client_meta(request)
    user, temp_password, expires_at = await AdminService(SessionService(redis)).reset_password(
        db,
        target_id=user_id,
        acting=acting,
        ip_address=ip,
        trace_id=getattr(request.state, "trace_id", None),
    )
    return ResetPasswordResponse(
        user=await _detail(db, user.id),
        initial_password=temp_password,
        expires_at=expires_at,
    )


@router.get(
    "/roles",
    response_model=list[RoleOut],
    summary="Read-only role -> permission matrix (drives the provisioning dropdown)",
    dependencies=[require_permission("users:manage")],
)
async def list_roles(db: DbDep) -> list[RoleOut]:
    roles = (await db.execute(select(AuthRole).order_by(AuthRole.id))).scalars().all()
    perms_by_role = await _permissions_by_role(db, {r.id for r in roles})
    return [
        RoleOut(
            id=r.id,
            name=r.name,
            description=r.description,
            is_system=r.is_system,
            permissions=sorted(perms_by_role.get(r.id, [])),
        )
        for r in roles
    ]


@router.get(
    "/audit",
    response_model=PagedResponse[AuditEventOut],
    summary="Search audit events",
    dependencies=[require_permission("audit:read")],
)
async def list_audit(
    db: DbDep, params: AuditPageParams = Depends()
) -> PagedResponse[AuditEventOut]:
    conds = []
    if params.user_id:
        conds.append(AuthAuditEvent.user_id == params.user_id)
    if params.event_type:
        conds.append(AuthAuditEvent.event_type == params.event_type)

    count_stmt = select(func.count()).select_from(AuthAuditEvent)
    list_stmt = select(AuthAuditEvent).order_by(AuthAuditEvent.created_at.desc())
    if conds:
        count_stmt = count_stmt.where(*conds)
        list_stmt = list_stmt.where(*conds)

    total = (await db.execute(count_stmt)).scalar_one()
    rows = (
        (await db.execute(list_stmt.offset(params.offset).limit(params.page_size))).scalars().all()
    )
    items = [AuditEventOut.model_validate(r) for r in rows]
    return PagedResponse.build(items, total, params)
