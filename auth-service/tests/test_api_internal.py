"""Integration tests for internal Gateway API endpoints."""

import pytest
from httpx import AsyncClient

from app.config import settings
from app.core.jwt_signer import key_manager


async def _register_and_login(client: AsyncClient) -> tuple[str, str]:
    """Register and login, returning (session_id, csrf_token) from response cookies."""
    csrf_resp = await client.get("/auth/csrf")
    assert csrf_resp.status_code == 200
    csrf_token = csrf_resp.json()["csrf_token"]

    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": "internal_test_user",
            "email": "internal_test@example.com",
            "password": "InternalTestPassword123!",
        },
        headers={"X-CSRF-Token": csrf_token},
    )
    assert reg_resp.status_code == 201

    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": "internal_test_user", "password": "InternalTestPassword123!"},
        headers={"X-CSRF-Token": csrf_token},
    )
    assert login_resp.status_code == 200

    session_id = login_resp.cookies["stockbot_session"]
    session_csrf = login_resp.cookies["stockbot_csrf"]
    return session_id, session_csrf


@pytest.mark.asyncio
async def test_internal_session_introspect_and_assertion(client: AsyncClient) -> None:
    """Test Gateway introspection and Principal Assertion signing."""
    session_id, csrf_token = await _register_and_login(client)

    # 2. Introspect valid session
    intro_resp = await client.post(
        "/internal/session/introspect",
        json={
            "session_id": session_id,
            "csrf_token": csrf_token,
        },
    )
    assert intro_resp.status_code == 200
    intro_data = intro_resp.json()
    assert intro_data["active"] is True
    assert intro_data["username"] == "internal_test_user"
    assert intro_data["csrf_valid"] is True
    assert "stocks:read" in intro_data["permissions"]

    # 3. Introspect invalid session
    bad_intro = await client.post(
        "/internal/session/introspect",
        json={"session_id": "sess_non_existent_999"},
    )
    assert bad_intro.status_code == 200
    assert bad_intro.json()["active"] is False

    # 4. Sign Principal Assertion JWT
    assertion_resp = await client.post(
        "/internal/principal/assertion",
        json={
            "session_id": session_id,
            "ttl": 60,
        },
    )
    assert assertion_resp.status_code == 200
    ass_data = assertion_resp.json()
    assert "assertion_token" in ass_data
    assert ass_data["expires_in"] == 60
    assert ass_data["algorithm"] == "RS256"

    # 5. Verify the assertion JWT locally
    payload = key_manager.verify_assertion(ass_data["assertion_token"])
    assert payload["username"] == "internal_test_user"
    assert payload["session_id"] == session_id
    assert payload["iss"] == "stock-bot-auth"
    assert payload["aud"] == "urn:stock-bot:api"
    assert "stocks:read" in payload["permissions"]


@pytest.mark.asyncio
async def test_internal_token_enforcement(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """X-Internal-Token is enforced once settings.internal_api_token is configured."""
    monkeypatch.setattr(settings, "internal_api_token", "super-secret-internal-token")

    # 1. Missing header -> 401
    missing = await client.post(
        "/internal/session/introspect",
        json={"session_id": "sess_any"},
    )
    assert missing.status_code == 401
    assert missing.json()["code"] == "AUTH_UNAUTHORIZED"

    # 2. Wrong token -> 401
    wrong = await client.post(
        "/internal/session/introspect",
        json={"session_id": "sess_any"},
        headers={"X-Internal-Token": "not-the-right-token"},
    )
    assert wrong.status_code == 401
    assert wrong.json()["code"] == "AUTH_UNAUTHORIZED"

    # 3. Correct token -> passes through (200, active=false for unknown session)
    ok = await client.post(
        "/internal/session/introspect",
        json={"session_id": "sess_non_existent_999"},
        headers={"X-Internal-Token": "super-secret-internal-token"},
    )
    assert ok.status_code == 200
    assert ok.json()["active"] is False

    # 4. Assertion endpoint guarded as well (router-level dependency)
    guarded = await client.post(
        "/internal/principal/assertion",
        json={"session_id": "sess_any"},
    )
    assert guarded.status_code == 401


@pytest.mark.asyncio
async def test_internal_token_disabled_by_default(client: AsyncClient) -> None:
    """Empty internal_api_token (default) = enforcement disabled (dev/test mode)."""
    assert settings.internal_api_token == ""
    resp = await client.post(
        "/internal/session/introspect",
        json={"session_id": "sess_non_existent_999"},
    )
    assert resp.status_code == 200
