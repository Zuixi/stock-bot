"""Integration tests for Auth API endpoints."""

import pytest
from httpx import AsyncClient

from tests.conftest import create_user


async def _fetch_csrf(client: AsyncClient) -> str:
    """Obtain an anonymous CSRF token via GET /auth/csrf (sets stockbot_csrf cookie)."""
    csrf_resp = await client.get("/auth/csrf")
    assert csrf_resp.status_code == 200
    return csrf_resp.json()["csrf_token"]


async def _register_and_login(
    client: AsyncClient,
    db_session,
    username: str,
    email: str,
    password: str,
) -> None:
    """Seed user directly in DB (register is invite-only now) and login."""
    await create_user(db_session, username=username, email=email, password=password)
    csrf_token = await _fetch_csrf(client)
    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": password},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_register_disabled_and_auth_flow(client: AsyncClient, db_session) -> None:
    """Invite-only: register is 403; the seeded user logs in and reads /auth/session."""
    username = "trader_alice"
    password = "SuperSecretPassword123!"

    # Self-registration is permanently closed (ADR 0010)
    csrf_token = await _fetch_csrf(client)
    reg_resp = await client.post(
        "/auth/register",
        json={"username": username, "email": "alice@example.com", "password": password},
        headers={"X-Request-Id": "req-reg-001", "X-CSRF-Token": csrf_token},
    )
    assert reg_resp.status_code == 403
    assert reg_resp.json()["code"] == "AUTH_REGISTRATION_DISABLED"

    # Login with wrong password (401)
    bad_login = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": "WrongPassword999!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert bad_login.status_code == 401
    assert bad_login.json()["code"] == "AUTH_INVALID_CREDENTIALS"

    # Seed the account (as an admin would) and log in
    user = await create_user(
        db_session, username=username, email="alice@example.com", password=password
    )
    assert user.status == "active"

    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": password},
        headers={"X-Request-Id": "req-login-2", "X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["user"]["username"] == username
    assert login_data["expires_in"] > 0
    assert "session_id" not in login_data
    assert "csrf_token" not in login_data
    assert "stockbot_session" in client.cookies
    assert "stockbot_csrf" in client.cookies

    # Session read
    session_resp = await client.get("/auth/session")
    assert session_resp.status_code == 200
    assert session_resp.json()["username"] == username

    # Logout
    session_csrf = client.cookies.get("stockbot_csrf")
    logout_resp = await client.post("/auth/logout", headers={"X-CSRF-Token": session_csrf})
    assert logout_resp.status_code == 200
    assert (await client.get("/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_login_without_csrf_rejected(client: AsyncClient) -> None:
    """Login without CSRF cookie/header must be rejected with 403 AUTH_CSRF_FAILED."""
    resp = await client.post(
        "/auth/login",
        json={"username_or_email": "someone", "password": "WhateverPass123!"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"
    assert resp.json()["message"] == "CSRF 校验失败"


@pytest.mark.asyncio
async def test_register_without_csrf_rejected(client: AsyncClient) -> None:
    """Register without CSRF cookie/header must be rejected with 403."""
    resp = await client.post(
        "/auth/register",
        json={
            "username": "csrfless_user",
            "email": "csrfless@example.com",
            "password": "SomePassword123!",
        },
    )
    # CSRF dependency runs first; without it the request never reaches the
    # invite-only rejection.
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"


@pytest.mark.asyncio
async def test_login_csrf_mismatch_rejected(client: AsyncClient) -> None:
    """CSRF cookie/header mismatch must be rejected (double-submit fails)."""
    await _fetch_csrf(client)
    resp = await client.post(
        "/auth/login",
        json={"username_or_email": "someone", "password": "WhateverPass123!"},
        headers={"X-CSRF-Token": "c_forged_mismatched_token"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"


@pytest.mark.asyncio
async def test_login_double_submit_flow_succeeds(client: AsyncClient, db_session) -> None:
    """GET /auth/csrf first, then login with the echoed token -> 200."""
    username = "double_submit_user"
    await create_user(db_session, username=username, password="DoubleSubmitPass123!")
    csrf_token = await _fetch_csrf(client)

    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": "DoubleSubmitPass123!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200
    assert "csrf_token" not in login_resp.json()
    assert "stockbot_session" in client.cookies


@pytest.mark.asyncio
async def test_logout_csrf_enforcement(client: AsyncClient, db_session) -> None:
    """Logout with a live session requires the session-bound CSRF token."""
    await _register_and_login(
        client, db_session, "logout_user", "logout@example.com", "LogoutPass123!"
    )
    csrf_token = client.cookies.get("stockbot_csrf")
    assert csrf_token

    # Missing token -> 403
    no_token = await client.post("/auth/logout")
    assert no_token.status_code == 403
    assert no_token.json()["code"] == "AUTH_CSRF_FAILED"

    # Wrong token -> 403
    wrong_token = await client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": "c_wrong_token"},
    )
    assert wrong_token.status_code == 403

    # Correct token -> 200 and session revoked
    ok = await client.post("/auth/logout", headers={"X-CSRF-Token": csrf_token})
    assert ok.status_code == 200
    assert "已成功退出" in ok.json()["message"]
    assert (await client.get("/auth/session")).status_code == 401


@pytest.mark.asyncio
async def test_logout_without_session_skips_csrf(client: AsyncClient) -> None:
    """Cookie-only logout (no live session) skips CSRF check and just clears cookies."""
    resp = await client.post("/auth/logout")
    assert resp.status_code == 200
    assert "已成功退出" in resp.json()["message"]


@pytest.mark.asyncio
async def test_x_session_id_header_bypass_removed(client: AsyncClient, db_session) -> None:
    """X-Session-Id header must no longer grant access — Cookie is the only source."""
    await _register_and_login(
        client, db_session, "bypass_user", "bypass@example.com", "BypassPass123!"
    )
    session_id = client.cookies.get("stockbot_session")
    assert session_id

    # Drop cookies, replay session id via the removed header -> 401
    client.cookies.clear()
    resp = await client.get("/auth/session", headers={"X-Session-Id": session_id})
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_UNAUTHORIZED"


class _FakeRequest:
    """Minimal stand-in for starlette Request (only what _extract_client_meta touches)."""

    def __init__(self, headers: dict[str, str], client_host: str | None) -> None:
        from types import SimpleNamespace

        self.headers = headers
        self.client = SimpleNamespace(host=client_host) if client_host else None


def test_extract_client_meta_trust_forwarded_for_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """X-Forwarded-For is honored only when settings.trust_forwarded_for is enabled."""
    from app.api.auth import _extract_client_meta
    from app.config import settings

    request = _FakeRequest(
        headers={"X-Forwarded-For": "198.51.100.7, 10.0.0.1"},
        client_host="203.0.113.9",
    )

    # Behind a trusted proxy: first hop of X-Forwarded-For wins
    monkeypatch.setattr(settings, "trust_forwarded_for", True)
    ip, _ = _extract_client_meta(request)
    assert ip == "198.51.100.7"

    # Direct exposure: spoofable header must be ignored, socket peer used instead
    monkeypatch.setattr(settings, "trust_forwarded_for", False)
    ip, _ = _extract_client_meta(request)
    assert ip == "203.0.113.9"


def test_extract_client_meta_no_forwarded_header_falls_back_to_peer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without X-Forwarded-For present, peer address is used in both modes."""
    from app.api.auth import _extract_client_meta
    from app.config import settings

    request = _FakeRequest(headers={}, client_host="192.0.2.55")
    for trusted in (True, False):
        monkeypatch.setattr(settings, "trust_forwarded_for", trusted)
        ip, _ = _extract_client_meta(request)
        assert ip == "192.0.2.55"


# ── 自助改资料（/auth/profile）─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_profile_self_service(client: AsyncClient, db_session) -> None:
    """Logged-in user edits own display name / username / email; audit recorded."""
    await _register_and_login(client, db_session, "profile_me", "me@example.com", "ProfilePass123!")
    csrf = client.cookies.get("stockbot_csrf")

    resp = await client.patch(
        "/auth/profile",
        json={"display_name": "新名字", "username": "profile_renamed"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["username"] == "profile_renamed"
    assert body["display_name"] == "新名字"
    # 邮箱未提交 → 保持不变
    assert body["email"] == "me@example.com"

    # 会话快照同步了用户名（断言 claims 取的就是这份）
    sess = (await client.get("/auth/session")).json()
    assert sess["username"] == "profile_renamed"


@pytest.mark.asyncio
async def test_update_profile_keeps_own_values(client: AsyncClient, db_session) -> None:
    """Submitting your own current username/email must not 409 against yourself."""
    await _register_and_login(client, db_session, "same_me", "same@example.com", "SamePass123!")
    csrf = client.cookies.get("stockbot_csrf")
    resp = await client.patch(
        "/auth/profile",
        json={"username": "same_me", "email": "same@example.com", "display_name": "X"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_update_profile_rejects_taken_identity(client: AsyncClient, db_session) -> None:
    await create_user(db_session, username="occupied", email="occupied@example.com")
    await _register_and_login(client, db_session, "wants_it", "wants@example.com", "WantsPass123!")
    csrf = client.cookies.get("stockbot_csrf")

    clash = await client.patch(
        "/auth/profile",
        json={"username": "occupied"},
        headers={"X-CSRF-Token": csrf},
    )
    assert clash.status_code == 409
    assert clash.json()["code"] == "RESOURCE_ALREADY_EXISTS"


@pytest.mark.asyncio
async def test_update_profile_requires_auth_and_csrf(client: AsyncClient, db_session) -> None:
    await create_user(db_session, username="no_csrf_profile", password="NoCsrf123!")

    # 未登录 → 401
    assert (await client.patch("/auth/profile", json={"display_name": "x"})).status_code == 401

    # 已登录但缺 CSRF → 403
    csrf = await _fetch_csrf(client)
    await client.post(
        "/auth/login",
        json={"username_or_email": "no_csrf_profile", "password": "NoCsrf123!"},
        headers={"X-CSRF-Token": csrf},
    )
    resp = await client.patch("/auth/profile", json={"display_name": "x"})
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"


@pytest.mark.asyncio
async def test_update_profile_blocked_for_temp_password_session(
    client: AsyncClient, db_session
) -> None:
    """强制改密门控同样盖住 /auth/profile：临时密码会话先改密码才能改资料。"""
    from datetime import UTC, datetime, timedelta

    await create_user(
        db_session,
        username="gated_profile",
        password="GatedPass123!",
        must_change_password=True,
        temp_password_expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    csrf = await _fetch_csrf(client)
    login = await client.post(
        "/auth/login",
        json={"username_or_email": "gated_profile", "password": "GatedPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert login.status_code == 200
    session_csrf = client.cookies.get("stockbot_csrf")

    resp = await client.patch(
        "/auth/profile",
        json={"display_name": "不该成功"},
        headers={"X-CSRF-Token": session_csrf},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_PASSWORD_CHANGE_REQUIRED"
