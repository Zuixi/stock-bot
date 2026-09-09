"""Unit tests for SessionService lifecycle and Redis synchronization."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AuthUser
from app.services.session_service import SessionService
from tests.conftest import FakeRedis


@pytest.mark.asyncio
async def test_session_lifecycle(db_session: AsyncSession, fake_redis: FakeRedis) -> None:
    """Test session creation, lookup, CSRF validation, and revocation."""
    session_service = SessionService(fake_redis)

    # 1. Create a test user in DB
    user = AuthUser(
        id=uuid.uuid4(),
        username="session_test_user",
        email="sess_test@example.com",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()

    # 2. Create session
    roles = ["trader"]
    permissions = ["stocks:read", "watchlists:write"]
    session_id, csrf_token, ttl = await session_service.create_session(
        db=db_session,
        user_id=user.id,
        username=user.username,
        roles=roles,
        permissions=permissions,
        ip_address="127.0.0.1",
        user_agent="pytest/1.0",
    )

    assert session_id.startswith("sess_")
    assert csrf_token.startswith("c_")
    assert ttl > 0

    # 3. Lookup session
    sess_data = await session_service.get_session(session_id)
    assert sess_data is not None
    assert sess_data["user_id"] == str(user.id)
    assert sess_data["username"] == user.username
    assert sess_data["roles"] == roles
    assert sess_data["permissions"] == permissions
    assert sess_data["csrf_token"] == csrf_token

    # 4. Introspect session with valid CSRF
    intro_valid = await session_service.introspect_session(session_id, csrf_token=csrf_token)
    assert intro_valid["active"] is True
    assert intro_valid["csrf_valid"] is True
    assert intro_valid["username"] == user.username

    # 5. Introspect with invalid CSRF
    intro_invalid_csrf = await session_service.introspect_session(
        session_id,
        csrf_token="c_wrong_csrf_token",
    )
    assert intro_invalid_csrf["active"] is True
    assert intro_invalid_csrf["csrf_valid"] is False

    # 6. Revoke session
    await session_service.revoke_session(db_session, session_id)
    sess_after_revoke = await session_service.get_session(session_id)
    assert sess_after_revoke is None

    intro_after_revoke = await session_service.introspect_session(session_id)
    assert intro_after_revoke["active"] is False


@pytest.mark.asyncio
async def test_revoke_all_user_sessions(db_session: AsyncSession, fake_redis: FakeRedis) -> None:
    """Test revoking all multi-device sessions for a single user."""
    session_service = SessionService(fake_redis)

    user = AuthUser(
        id=uuid.uuid4(),
        username="multidevice_user",
        email="multidevice@example.com",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()

    # Create 3 sessions
    sess1, _, _ = await session_service.create_session(
        db_session,
        user.id,
        user.username,
        ["viewer"],
        ["stocks:read"],
    )
    sess2, _, _ = await session_service.create_session(
        db_session,
        user.id,
        user.username,
        ["viewer"],
        ["stocks:read"],
    )
    sess3, _, _ = await session_service.create_session(
        db_session,
        user.id,
        user.username,
        ["viewer"],
        ["stocks:read"],
    )

    # Revoke all
    count = await session_service.revoke_all_user_sessions(db_session, user.id)
    assert count == 3

    assert await session_service.get_session(sess1) is None
    assert await session_service.get_session(sess2) is None
    assert await session_service.get_session(sess3) is None
