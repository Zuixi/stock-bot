"""Integration tests for Auth API endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_auth_full_flow(client: AsyncClient) -> None:
    """Test full auth lifecycle: Register -> Login -> Session -> CSRF -> Logout."""
    username = "trader_alice"
    email = "alice@example.com"
    password = "SuperSecretPassword123!"

    # 1. Register
    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": email,
            "password": password,
            "display_name": "Alice Trader",
        },
        headers={"X-Request-Id": "req-register-001"},
    )
    assert reg_resp.status_code == 201
    user_data = reg_resp.json()
    assert user_data["username"] == username
    assert user_data["email"] == email
    assert "trader" in user_data["roles"]
    assert "stocks:read" in user_data["permissions"]
    assert reg_resp.headers.get("X-Request-Id") == "req-register-001"

    # 2. Duplicate Register (Conflict 409)
    dup_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": "another@example.com",
            "password": password,
        },
    )
    assert dup_resp.status_code == 409
    err = dup_resp.json()
    assert err["code"] == "RESOURCE_ALREADY_EXISTS"
    assert "已被注册" in err["message"]
    assert "trace_id" in err

    # 3. Login with wrong password (401)
    bad_login = await client.post(
        "/auth/login",
        json={
            "username_or_email": username,
            "password": "WrongPassword999!",
        },
    )
    assert bad_login.status_code == 401
    bad_err = bad_login.json()
    assert bad_err["code"] == "AUTH_INVALID_CREDENTIALS"

    # 4. Login with valid credentials
    login_resp = await client.post(
        "/auth/login",
        json={
            "username_or_email": username,
            "password": password,
        },
        headers={"X-Request-Id": "req-login-002"},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    assert "session_id" in login_data
    assert "csrf_token" in login_data
    assert login_data["user"]["username"] == username

    session_id = login_data["session_id"]
    csrf_token = login_data["csrf_token"]

    # Verify cookies
    cookies = login_resp.cookies
    assert "stockbot_session" in cookies
    assert "stockbot_csrf" in cookies
    assert cookies["stockbot_session"] == session_id
    assert cookies["stockbot_csrf"] == csrf_token

    # 5. Access /auth/session with session cookie
    sess_resp = await client.get(
        "/auth/session",
        cookies={"stockbot_session": session_id},
    )
    assert sess_resp.status_code == 200
    sess_user = sess_resp.json()
    assert sess_user["username"] == username
    assert "stocks:read" in sess_user["permissions"]

    # 6. Access /auth/me alias
    me_resp = await client.get(
        "/auth/me",
        cookies={"stockbot_session": session_id},
    )
    assert me_resp.status_code == 200
    assert me_resp.json()["username"] == username

    # 7. Get CSRF token
    csrf_resp = await client.get(
        "/auth/csrf",
        cookies={"stockbot_session": session_id},
    )
    assert csrf_resp.status_code == 200
    assert csrf_resp.json()["csrf_token"] == csrf_token

    # 8. List active sessions
    sessions_resp = await client.get(
        "/auth/sessions",
        cookies={"stockbot_session": session_id},
    )
    assert sessions_resp.status_code == 200
    sessions_list = sessions_resp.json()
    assert len(sessions_list) >= 1
    assert any(s["id"] == session_id for s in sessions_list)

    # 9. Logout
    logout_resp = await client.post(
        "/auth/logout",
        cookies={"stockbot_session": session_id},
    )
    assert logout_resp.status_code == 200
    assert "已成功退出" in logout_resp.json()["message"]

    # 10. Verify session is now invalid (401)
    unauth_resp = await client.get(
        "/auth/session",
        cookies={"stockbot_session": session_id},
    )
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["code"] == "AUTH_UNAUTHORIZED"
