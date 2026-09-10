"""Integration tests for Auth API endpoints."""

import pytest
from httpx import AsyncClient


async def _fetch_csrf(client: AsyncClient) -> str:
    """Obtain an anonymous CSRF token via GET /auth/csrf (sets stockbot_csrf cookie)."""
    csrf_resp = await client.get("/auth/csrf")
    assert csrf_resp.status_code == 200
    return csrf_resp.json()["csrf_token"]


async def _register_and_login(
    client: AsyncClient,
    username: str,
    email: str,
    password: str,
) -> None:
    """Register and login with double-submit CSRF headers (cookies stay in the client jar)."""
    csrf_token = await _fetch_csrf(client)
    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert reg_resp.status_code == 201
    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": password},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200


@pytest.mark.asyncio
async def test_auth_full_flow(client: AsyncClient) -> None:
    """Test full auth lifecycle: CSRF -> Register -> Login -> Session -> Logout."""
    username = "trader_alice"
    email = "alice@example.com"
    password = "SuperSecretPassword123!"

    # 1. Anonymous CSRF double-submit bootstrap (cookie + token)
    csrf_token = await _fetch_csrf(client)
    assert "stockbot_csrf" in client.cookies

    # 2. Register
    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "display_name": "Alice Trader",
        },
        headers={"X-Request-Id": "req-register-001", "X-CSRF-Token": csrf_token},
    )
    assert reg_resp.status_code == 201
    user_data = reg_resp.json()
    assert user_data["username"] == username
    assert user_data["email"] == email
    assert "trader" in user_data["roles"]
    assert "stocks:read" in user_data["permissions"]
    assert reg_resp.headers.get("X-Request-Id") == "req-register-001"

    # 3. Duplicate Register (Conflict 409) — still requires CSRF header
    dup_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": "another@example.com",
            "password": password,
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert dup_resp.status_code == 409
    err = dup_resp.json()
    assert err["code"] == "RESOURCE_ALREADY_EXISTS"
    assert "已被注册" in err["message"]
    assert "trace_id" in err

    # 4. Login with wrong password (401)
    bad_login = await client.post(
        "/auth/login",
        json={
            "username_or_email": username,
            "password": "WrongPassword999!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert bad_login.status_code == 401
    bad_err = bad_login.json()
    assert bad_err["code"] == "AUTH_INVALID_CREDENTIALS"

    # 5. Login with valid credentials
    login_resp = await client.post(
        "/auth/login",
        json={
            "username_or_email": username,
            "password": password,
        },
        headers={"X-Request-Id": "req-login-002", "X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert login_data["user"]["username"] == username
    assert login_data["expires_in"] > 0
    # Credentials must never leak in the response body — cookies only (P1-3)
    assert "session_id" not in login_data
    assert "csrf_token" not in login_data

    # Verify cookies carry the credentials
    cookies = login_resp.cookies
    assert "stockbot_session" in cookies
    assert "stockbot_csrf" in cookies
    session_id = cookies["stockbot_session"]
    csrf_token = cookies["stockbot_csrf"]
    assert session_id.startswith("sess_")
    assert csrf_token.startswith("c_")

    # 6. Access /auth/session with cookies only (no X-Session-Id header)
    sess_resp = await client.get("/auth/session")
    assert sess_resp.status_code == 200
    sess_user = sess_resp.json()
    assert sess_user["username"] == username
    assert "stocks:read" in sess_user["permissions"]

    # 7. Access /auth/me alias
    me_resp = await client.get("/auth/me")
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == username

    # 8. Get CSRF token (session-bound, equals cookie value)
    csrf_resp = await client.get("/auth/csrf")
    assert csrf_resp.status_code == 200
    assert csrf_resp.json()["csrf_token"] == csrf_token

    # 9. List active sessions
    sessions_resp = await client.get("/auth/sessions")
    assert sessions_resp.status_code == 200
    sessions_list = sessions_resp.json()
    assert len(sessions_list) >= 1
    assert any(s["id"] == session_id for s in sessions_list)

    # 10. Logout with session-bound CSRF token
    logout_resp = await client.post(
        "/auth/logout",
        headers={"X-CSRF-Token": csrf_token},
    )
    assert logout_resp.status_code == 200
    assert "已成功退出" in logout_resp.json()["message"]

    # 11. Verify session is now invalid (401)
    unauth_resp = await client.get("/auth/session")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == "AUTH_UNAUTHORIZED"


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
async def test_login_double_submit_flow_succeeds(client: AsyncClient) -> None:
    """GET /auth/csrf first, then login with the echoed token -> 200."""
    username = "double_submit_user"
    csrf_token = await _fetch_csrf(client)

    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": "double_submit@example.com",
            "password": "DoubleSubmitPass123!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert reg_resp.status_code == 201

    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": "DoubleSubmitPass123!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200
    assert "csrf_token" not in login_resp.json()
    assert "stockbot_session" in client.cookies


@pytest.mark.asyncio
async def test_logout_csrf_enforcement(client: AsyncClient) -> None:
    """Logout with a live session requires the session-bound CSRF token."""
    await _register_and_login(client, "logout_user", "logout@example.com", "LogoutPass123!")
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
async def test_x_session_id_header_bypass_removed(client: AsyncClient) -> None:
    """X-Session-Id header must no longer grant access — Cookie is the only source."""
    await _register_and_login(client, "bypass_user", "bypass@example.com", "BypassPass123!")
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
