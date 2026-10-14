"""Change-password flow tests: the five-segment forced gate (ADR 0010)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import create_user


async def _login(client: AsyncClient, username: str, password: str) -> str:
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    resp = await client.post(
        "/auth/login",
        json={"username_or_email": username, "password": password},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text
    return (await client.get("/auth/csrf")).json()["csrf_token"]


@pytest.mark.asyncio
async def test_change_password_clears_gate_and_kicks_other_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(
        db_session,
        username="temp_user",
        password="TempPass123!",
        must_change_password=True,
        temp_password_expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    session_csrf = await _login(client, "temp_user", "TempPass123!")

    # gate active: non-allowlisted /auth route blocked
    blocked = await client.get("/auth/sessions")
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "AUTH_PASSWORD_CHANGE_REQUIRED"

    # change password (allowlisted)
    resp = await client.post(
        "/auth/change-password",
        json={"current_password": "TempPass123!", "new_password": "MyOwnPass456!"},
        headers={"X-CSRF-Token": session_csrf},
    )
    assert resp.status_code == 200

    # gate lifted for the same session, no re-login
    assert (await client.get("/auth/sessions")).status_code == 200
    profile = (await client.get("/auth/session")).json()
    assert profile["must_change_password"] is False

    # old password no longer works for future logins
    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    old = await client.post(
        "/auth/login",
        json={"username_or_email": "temp_user", "password": "TempPass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert old.status_code == 401
    new = await client.post(
        "/auth/login",
        json={"username_or_email": "temp_user", "password": "MyOwnPass456!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert new.status_code == 200
    assert new.json()["user"]["must_change_password"] is False


@pytest.mark.asyncio
async def test_change_password_requires_csrf(client: AsyncClient, db_session: AsyncSession) -> None:
    await create_user(db_session, username="csrf_pw", password="CsrfPass123!")
    await _login(client, "csrf_pw", "CsrfPass123!")
    resp = await client.post(
        "/auth/change-password",
        json={"current_password": "CsrfPass123!", "new_password": "NewPass456!"},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_CSRF_FAILED"


@pytest.mark.asyncio
async def test_change_password_wrong_current(client: AsyncClient, db_session: AsyncSession) -> None:
    await create_user(db_session, username="wrongcur", password="RightPass123!")
    session_csrf = await _login(client, "wrongcur", "RightPass123!")
    resp = await client.post(
        "/auth/change-password",
        json={"current_password": "NotRight999!", "new_password": "NewPass456!"},
        headers={"X-CSRF-Token": session_csrf},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == "AUTH_INVALID_CREDENTIALS"


@pytest.mark.asyncio
async def test_expired_temp_password_cannot_login(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await create_user(
        db_session,
        username="late_user",
        password="LatePass123!",
        must_change_password=True,
        temp_password_expires_at=datetime.now(UTC) - timedelta(hours=1),
    )
    client.cookies.clear()
    csrf = (await client.get("/auth/csrf")).json()["csrf_token"]
    resp = await client.post(
        "/auth/login",
        json={"username_or_email": "late_user", "password": "LatePass123!"},
        headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_TEMP_PASSWORD_EXPIRED"


@pytest.mark.asyncio
async def test_assertion_endpoint_refuses_must_change_sessions(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    """Gate segment 3: no Principal Assertion until the temp password is replaced.

    Regression: get_session() rebuilds its dict from an allowlist and used to
    drop must_change_password, so this endpoint kept signing assertions and
    the whole /api/* gate was a no-op in production.
    """
    from datetime import UTC, datetime, timedelta

    await create_user(
        db_session,
        username="gate_user",
        password="GatePass123!",
        must_change_password=True,
        temp_password_expires_at=datetime.now(UTC) + timedelta(hours=72),
    )
    await _login(client, "gate_user", "GatePass123!")

    session_id = client.cookies.get("stockbot_session")
    resp = await client.post(
        "/internal/principal/assertion",
        json={"session_id": session_id},
    )
    assert resp.status_code == 403
    assert resp.json()["code"] == "AUTH_PASSWORD_CHANGE_REQUIRED"
