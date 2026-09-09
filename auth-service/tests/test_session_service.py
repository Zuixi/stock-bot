"""Unit tests for SessionService lifecycle and Redis synchronization."""

import time
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
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


@pytest.mark.asyncio
async def test_absolute_session_ttl_expires(
    db_session: AsyncSession,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessions older than absolute_session_ttl must be force-logged-out (logout semantics)."""
    monkeypatch.setattr(settings, "absolute_session_ttl", 3600)  # 1h cap for test speed
    session_service = SessionService(fake_redis)

    user = AuthUser(
        id=uuid.uuid4(),
        username="absolute_ttl_user",
        email="absolute_ttl@example.com",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()

    session_id, _, _ = await session_service.create_session(
        db_session,
        user.id,
        user.username,
        ["viewer"],
        ["stocks:read"],
    )

    # Backdate created_at beyond the absolute cap (created 2h ago, cap is 1h)
    stale_ts = int(time.time()) - 7200
    await fake_redis.hset(f"session:{session_id}", "created_at", str(stale_ts))

    # get_session must return None (logout semantics)...
    assert await session_service.get_session(session_id) is None
    # ...delete the session hash...
    assert await fake_redis.hgetall(f"session:{session_id}") == {}
    # ...and remove the session from the user's session index.
    assert session_id not in await fake_redis.smembers(f"user_sessions:{user.id}")

    # introspect (forward-auth fast path) must report inactive as well
    intro = await session_service.introspect_session(session_id)
    assert intro["active"] is False


@pytest.mark.asyncio
async def test_absolute_session_ttl_not_reached_still_renews(
    db_session: AsyncSession,
    fake_redis: FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A young session keeps sliding-renewal semantics (no forced logout)."""
    monkeypatch.setattr(settings, "absolute_session_ttl", 3600)
    session_service = SessionService(fake_redis)

    user = AuthUser(
        id=uuid.uuid4(),
        username="fresh_session_user",
        email="fresh_session@example.com",
        status="active",
    )
    db_session.add(user)
    await db_session.commit()

    session_id, _, _ = await session_service.create_session(
        db_session,
        user.id,
        user.username,
        ["viewer"],
        ["stocks:read"],
    )

    sess = await session_service.get_session(session_id)
    assert sess is not None
    assert sess["user_id"] == str(user.id)

    # Sliding renewal advanced last_seen_at and the session stays indexed
    assert sess["last_seen_at"] >= sess["created_at"]
    assert session_id in await fake_redis.smembers(f"user_sessions:{user.id}")
