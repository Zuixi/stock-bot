"""Admin console API tests (ADR 0010).

Covers the full behavioral contract plus the parameterized guard matrix:
every admin route must 403 without its permission, and every state-changing
route must 403 without X-CSRF-Token. A new endpoint added to the router
without its guard turns this suite red.
"""

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.admin import ADMIN_ROUTES
from tests.conftest import create_user

ADMIN = ("admin",)
VIEWER = ("viewer",)


async def _login_admin(client: AsyncClient, db_session: AsyncSession) -> None:
    """Seed an admin and leave the client logged in with its session cookie."""
    await create_user(db_session, username="root_admin", password="AdminPass123!", roles=ADMIN)
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    resp = await client.post(
        "/auth/login",
        json={"username_or_email": "root_admin", "password": "AdminPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    # refresh csrf to the session-bound one for later write calls
    csrf2 = (await client.get("/auth/csrf")).json()["csrf_token"]
    client.headers["X-CSRF-Token"] = csrf2


def _path(template: str) -> str:
    return template.format(user_id=str(uuid.uuid4()))


def _body_for(path: str) -> dict | None:
    if path.endswith("/suspend"):
        return {"reason": "test"}
    if path.endswith("/roles"):
        return {"roles": ["viewer"]}
    if path == "/auth/admin/users":
        return {
            "username": "u_x",
            "email": "x@example.com",
            "roles": ["viewer"],
        }
    return None


# ── guard matrix: iterates the route table ──────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,permission,is_write", ADMIN_ROUTES)
async def test_admin_routes_reject_anonymous(
    client: AsyncClient, method: str, path: str, permission: str, is_write: bool
) -> None:
    """Every admin route must 401 without a session."""
    url = _path(path)
    if is_write:
        resp = await client.request(method, url, json=_body_for(url))
    else:
        resp = await client.request(method, url)
    assert resp.status_code == 401, f"{method} {url} leaked to anonymous"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,permission,is_write", ADMIN_ROUTES)
async def test_admin_routes_reject_unprivileged(
    client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    path: str,
    permission: str,
    is_write: bool,
) -> None:
    """Every admin route must 403 for a user lacking its permission."""
    await create_user(db_session, username="plain_trader", password="TraderPass123!")
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    login = await client.post(
        "/auth/login",
        json={"username_or_email": "plain_trader", "password": "TraderPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200
    session_csrf = (await client.get("/auth/csrf")).json()["csrf_token"]

    url = _path(path)
    headers = {"X-CSRF-Token": session_csrf} if is_write else {}
    if is_write:
        resp = await client.request(method, url, json=_body_for(url), headers=headers)
    else:
        resp = await client.request(method, url, headers=headers)
    assert resp.status_code == 403, f"{method} {url} reachable without {permission}"
    assert resp.json()["code"] == "AUTH_FORBIDDEN"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "method,path,permission,is_write",
    [r for r in ADMIN_ROUTES if r[3]],
)
async def test_admin_write_routes_reject_missing_csrf(
    client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    path: str,
    permission: str,
    is_write: bool,
) -> None:
    """Every state-changing admin route must 403 without X-CSRF-Token."""
    await _login_admin(client, db_session)
    client.headers.pop("X-CSRF-Token", None)
    url = _path(path)
    resp = await client.request(method, url, json=_body_for(url))
    assert resp.status_code == 403, f"{method} {url} accepted missing CSRF"
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"


# ── provisioning ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_provision_user_full_flow(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)

    resp = await client.post(
        "/auth/admin/users",
        json={
            "username": "invited_analyst",
            "email": "analyst@corp.com",
            "display_name": "分析师甲",
            "roles": ["analyst"],
        },
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["user"]["status"] == "active"
    assert data["user"]["roles"] == ["analyst"]
    assert data["user"]["must_change_password"] is True
    assert "research:read" in data["user"]["permissions"]
    temp_password = data["initial_password"]
    assert len(temp_password) >= 8
    assert data["expires_at"] is not None

    # New user logs in with the temp password immediately
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    login = await client.post(
        "/auth/login",
        json={"username_or_email": "invited_analyst", "password": temp_password},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200
    assert login.json()["user"]["must_change_password"] is True

    # ... and is immediately funneled to change-password (segment 2)
    blocked = await client.get("/auth/sessions")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "AUTH_PASSWORD_CHANGE_REQUIRED"
    # allowlisted routes still work
    assert (await client.get("/auth/session")).status_code == 200


@pytest.mark.asyncio
async def test_provision_requires_roles(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    resp = await client.post(
        "/auth/admin/users",
        json={"username": "no_role_user", "email": "nr@example.com", "roles": []},
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_provision_unknown_role_404(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    resp = await client.post(
        "/auth/admin/users",
        json={"username": "ghost_role", "email": "g@example.com", "roles": ["nonexistent"]},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_provision_duplicate_username_409(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login_admin(client, db_session)
    body = {"username": "invited_analyst", "email": "someone@example.com", "roles": ["viewer"]}
    first = await client.post("/auth/admin/users", json=body)
    assert first.status_code == 201
    dup = await client.post("/auth/admin/users", json={**body, "email": "other@example.com"})
    assert dup.status_code == 409


# ── lifecycle ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suspend_reactivate_and_session_revocation(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login_admin(client, db_session)
    created = (
        await client.post(
            "/auth/admin/users",
            json={"username": "victim", "email": "v@example.com", "roles": ["viewer"]},
        )
    ).json()
    uid = created["user"]["id"]

    # victim logs in; save their cookies to simulate their device later
    client.cookies.clear()
    client.headers.pop("X-CSRF-Token", None)
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    await client.post(
        "/auth/login",
        json={"username_or_email": "victim", "password": created["initial_password"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert (await client.get("/auth/session")).status_code == 200
    victim_cookies = dict(client.cookies)

    # admin suspends victim from the same client (fresh login as admin)
    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    await client.post(
        "/auth/login",
        json={"username_or_email": "root_admin", "password": "AdminPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    session_csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    susp = await client.post(
        f"/auth/admin/users/{uid}/suspend",
        json={"reason": "违规使用"},
        headers={"X-CSRF-Token": session_csrf},
    )
    assert susp.status_code == 200
    assert susp.json()["status"] == "suspended"
    assert susp.json()["status_reason"] == "违规使用"

    # victim's saved device session is dead immediately on /auth/*
    client.cookies.clear()
    client.cookies.update(victim_cookies)
    assert (await client.get("/auth/session")).status_code == 401

    # suspended login denied only with correct password (password-first order)
    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    wrong = await client.post(
        "/auth/login",
        json={"username_or_email": "victim", "password": "WrongPass999!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "AUTH_INVALID_CREDENTIALS"
    right = await client.post(
        "/auth/login",
        json={"username_or_email": "victim", "password": created["initial_password"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert right.status_code == 403
    assert right.json()["code"] == "AUTH_ACCOUNT_SUSPENDED"
    assert "违规使用" in right.json()["message"]

    # reactivate -> login works again (still must-change gated)
    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    await client.post(
        "/auth/login",
        json={"username_or_email": "root_admin", "password": "AdminPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    session_csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    back = await client.post(
        f"/auth/admin/users/{uid}/reactivate",
        headers={"X-CSRF-Token": session_csrf},
    )
    assert back.status_code == 200
    assert back.json()["status"] == "active"

    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    ok = await client.post(
        "/auth/login",
        json={"username_or_email": "victim", "password": created["initial_password"]},
        headers={"X-CSRF-Token": csrf},
    )
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_suspend_requires_active(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    user = await create_user(db_session, username="already_suspended", status="suspended")
    resp = await client.post(f"/auth/admin/users/{user.id}/suspend", json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_STATE_TRANSITION"


@pytest.mark.asyncio
async def test_cannot_suspend_self(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    me = (await client.get("/auth/session")).json()
    resp = await client.post(f"/auth/admin/users/{me['id']}/suspend", json={})
    assert resp.status_code == 409
    assert resp.json()["code"] == "INVALID_OPERATION"


@pytest.mark.asyncio
async def test_replace_roles(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    created = (
        await client.post(
            "/auth/admin/users",
            json={"username": "role_swap", "email": "rs@example.com", "roles": ["viewer"]},
        )
    ).json()
    uid = created["user"]["id"]

    resp = await client.put(f"/auth/admin/users/{uid}/roles", json={"roles": ["analyst"]})
    assert resp.status_code == 200
    assert resp.json()["roles"] == ["analyst"]
    assert "research:read" in resp.json()["permissions"]

    # empty roles rejected
    empty = await client.put(f"/auth/admin/users/{uid}/roles", json={"roles": []})
    assert empty.status_code == 422


@pytest.mark.asyncio
async def test_reset_password_revokes_and_reissues(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login_admin(client, db_session)
    created = (
        await client.post(
            "/auth/admin/users",
            json={"username": "pw_target", "email": "pw@example.com", "roles": ["viewer"]},
        )
    ).json()
    uid = created["user"]["id"]

    resp = await client.post(f"/auth/admin/users/{uid}/reset-password")
    assert resp.status_code == 200
    new_pw = resp.json()["initial_password"]
    assert new_pw != created["initial_password"]

    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    login = await client.post(
        "/auth/login",
        json={"username_or_email": "pw_target", "password": new_pw},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200


# ── read models ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_users_filter_and_search(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    await create_user(db_session, username="searchable_zhang", roles=("viewer",))

    # search
    found = await client.get("/auth/admin/users", params={"q": "searchable"})
    assert found.status_code == 200
    usernames = [u["username"] for u in found.json()["items"]]
    assert "searchable_zhang" in usernames

    # status filter drives the badge contract: total present, page respected
    active = await client.get("/auth/admin/users", params={"status": "active", "page_size": 1})
    assert active.status_code == 200
    body = active.json()
    assert len(body["items"]) == 1
    assert body["total"] >= 2
    assert all(u["status"] == "active" for u in body["items"])


@pytest.mark.asyncio
async def test_roles_matrix_readonly(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    resp = await client.get("/auth/admin/roles")
    assert resp.status_code == 200
    roles = {r["id"]: r for r in resp.json()}
    assert "admin" in roles
    assert "viewer" in roles
    assert "stocks:read" in roles["viewer"]["permissions"]


@pytest.mark.asyncio
async def test_audit_search(client: AsyncClient, db_session: AsyncSession) -> None:
    await _login_admin(client, db_session)
    await client.post(
        "/auth/admin/users",
        json={"username": "audited_one", "email": "a@example.com", "roles": ["viewer"]},
    )
    resp = await client.get("/auth/admin/audit", params={"event_type": "auth.user.provisioned"})
    assert resp.status_code == 200
    events = resp.json()["items"]
    assert any(e["payload"].get("username") == "audited_one" for e in events)
    # no password material ever lands in the audit payload
    for e in events:
        assert "initial_password" not in (e["payload"] or {})
        assert "password_hash" not in (e["payload"] or {})


# ── guard-matrix integrity (the guard is only worth what it covers) ─────────


def test_admin_routes_table_matches_router() -> None:
    """ADMIN_ROUTES must mirror the real router, or the guard tests lie.

    Without this tripwire, adding an endpoint and forgetting the table entry
    leaves it silently uncovered while the suite stays green.
    """
    from app.api.admin import router

    declared = {path for _, path, _, _ in ADMIN_ROUTES}
    actual = {route.path for route in router.routes}
    assert declared == actual, f"ADMIN_ROUTES 与 router 漂移: {declared ^ actual}"


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path,permission,is_write", ADMIN_ROUTES)
async def test_admin_routes_enforce_the_correct_permission(
    client: AsyncClient,
    db_session: AsyncSession,
    method: str,
    path: str,
    permission: str,
    is_write: bool,
) -> None:
    """A probe holding ONLY roles:manage must reach exactly the roles route.

    This is what distinguishes "has a guard" from "has the RIGHT guard":
    a route wired to the wrong permission still 403s for a permission-less
    user, so the unprivileged test above cannot catch it.
    """
    await create_user(
        db_session, username="roles_probe", password="ProbePass123!", roles=("roles_only",)
    )
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    login = await client.post(
        "/auth/login",
        json={"username_or_email": "roles_probe", "password": "ProbePass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200
    session_csrf = (await client.get("/auth/csrf")).json()["csrf_token"]

    url = _path(path)
    headers = {"X-CSRF-Token": session_csrf} if is_write else {}
    body = _body_for(url)
    resp = await client.request(method, url, json=body, headers=headers)

    if permission == "roles:manage":
        assert resp.status_code != 403, f"{method} {url} 拒绝了本应放行的 roles:manage"
    else:
        assert resp.status_code == 403, f"{method} {url} 挂了错误的权限（期望 {permission}）"
