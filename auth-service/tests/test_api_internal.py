"""Integration tests for internal Gateway API endpoints."""

import pytest
from httpx import AsyncClient

from app.core.jwt_signer import key_manager


@pytest.mark.asyncio
async def test_internal_session_introspect_and_assertion(client: AsyncClient) -> None:
    """Test Gateway introspection and Principal Assertion signing."""
    # 1. Register & login a test user
    username = "internal_test_user"
    password = "InternalTestPassword123!"

    reg_resp = await client.post(
        "/auth/register",
        json={
            "username": username,
            "email": "internal_test@example.com",
            "password": password,
        },
    )
    assert reg_resp.status_code == 201

    login_resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": password},
    )
    assert login_resp.status_code == 200
    login_data = login_resp.json()
    session_id = login_data["session_id"]
    csrf_token = login_data["csrf_token"]

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
    assert intro_data["username"] == username
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
    assert payload["username"] == username
    assert payload["session_id"] == session_id
    assert payload["iss"] == "stock-auth-service"
    assert payload["aud"] == "stock-api"
    assert "stocks:read" in payload["permissions"]
