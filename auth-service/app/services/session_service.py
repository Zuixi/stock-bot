"""Session lifecycle and Redis/DB synchronization service."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.crypto import generate_csrf_token, generate_session_id, hash_token, verify_csrf_token
from app.models.session import AuthSession


class SessionService:
    """Manages session creation, lookup, extension, and revocation."""

    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.session_ttl = settings.session_ttl

    def _session_key(self, session_id: str) -> str:
        return f"session:{session_id}"

    def _user_sessions_key(self, user_id: str | uuid.UUID) -> str:
        return f"user_sessions:{user_id}"

    async def create_session(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        username: str,
        roles: list[str],
        permissions: list[str],
        ip_address: str | None = None,
        user_agent: str | None = None,
        device_fingerprint: str | None = None,
    ) -> tuple[str, str, int]:
        """Create a new session in Redis and snapshot it into DB.

        Returns (session_id, csrf_token, expires_in).
        """
        session_id = generate_session_id()
        csrf_token = generate_csrf_token()
        now = int(time.time())
        expires_at_dt = datetime.now(UTC) + timedelta(seconds=self.session_ttl)

        session_data: dict[str | bytes, bytes | float | int | str] = {
            "user_id": str(user_id),
            "username": username,
            "roles": json.dumps(roles),
            "permissions": json.dumps(permissions),
            "csrf_token": csrf_token,
            "ip_address": ip_address or "",
            "user_agent": user_agent or "",
            "created_at": str(now),
            "last_seen_at": str(now),
        }

        session_key = self._session_key(session_id)
        user_sessions_key = self._user_sessions_key(user_id)

        # 1. Write to Redis
        pipeline = self.redis.pipeline()
        pipeline.hset(session_key, mapping=session_data)
        pipeline.expire(session_key, self.session_ttl)
        pipeline.sadd(user_sessions_key, session_id)
        pipeline.expire(user_sessions_key, self.session_ttl * 7)  # keep index longer
        await pipeline.execute()

        # 2. Persist snapshot to DB
        db_session = AuthSession(
            id=session_id,
            user_id=user_id,
            ip_address=ip_address,
            user_agent=user_agent,
            device_fingerprint=device_fingerprint,
            csrf_token_hash=hash_token(csrf_token),
            is_revoked=False,
            expires_at=expires_at_dt,
            last_active_at=datetime.now(UTC),
        )
        db.add(db_session)
        await db.commit()

        return session_id, csrf_token, self.session_ttl

    async def get_session(
        self,
        session_id: str,
        extend_ttl: bool = True,
    ) -> dict[str, Any] | None:
        """Fetch session data from Redis and optionally extend TTL (sliding expiration)."""
        session_key = self._session_key(session_id)
        raw_data = await self.redis.hgetall(session_key)
        if not raw_data:
            return None

        # Parse JSON fields
        roles = json.loads(raw_data.get("roles", "[]"))
        permissions = json.loads(raw_data.get("permissions", "[]"))

        session_info: dict[str, Any] = {
            "session_id": session_id,
            "user_id": raw_data.get("user_id"),
            "username": raw_data.get("username"),
            "roles": roles,
            "permissions": permissions,
            "csrf_token": raw_data.get("csrf_token"),
            "ip_address": raw_data.get("ip_address"),
            "user_agent": raw_data.get("user_agent"),
            "created_at": int(raw_data.get("created_at", 0)),
            "last_seen_at": int(raw_data.get("last_seen_at", 0)),
        }

        if extend_ttl:
            now = int(time.time())
            pipeline = self.redis.pipeline()
            pipeline.hset(session_key, "last_seen_at", str(now))
            pipeline.expire(session_key, self.session_ttl)
            await pipeline.execute()

        return session_info

    async def introspect_session(
        self,
        session_id: str,
        csrf_token: str | None = None,
    ) -> dict[str, Any]:
        """Fast session introspection for Gateway."""
        session_data = await self.get_session(session_id, extend_ttl=True)
        if not session_data:
            return {"active": False}

        csrf_valid: bool | None = None
        if csrf_token is not None:
            stored_csrf = session_data.get("csrf_token", "")
            csrf_valid = verify_csrf_token(stored_csrf, csrf_token)

        ttl = await self.redis.ttl(self._session_key(session_id))
        if ttl < 0:
            ttl = self.session_ttl

        return {
            "active": True,
            "user_id": session_data["user_id"],
            "username": session_data["username"],
            "roles": session_data["roles"],
            "permissions": session_data["permissions"],
            "csrf_valid": csrf_valid,
            "expires_in": ttl,
        }

    async def revoke_session(
        self,
        db: AsyncSession,
        session_id: str,
    ) -> bool:
        """Revoke a single session in Redis and DB."""
        session_key = self._session_key(session_id)
        user_id_raw = await self.redis.hget(session_key, "user_id")

        pipeline = self.redis.pipeline()
        pipeline.delete(session_key)
        if user_id_raw:
            user_sessions_key = self._user_sessions_key(user_id_raw)
            pipeline.srem(user_sessions_key, session_id)
        await pipeline.execute()

        # Update DB snapshot
        stmt = (
            update(AuthSession)
            .where(AuthSession.id == session_id)
            .values(is_revoked=True, last_active_at=datetime.now(UTC))
        )
        await db.execute(stmt)
        await db.commit()
        return True

    async def revoke_all_user_sessions(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
    ) -> int:
        """Revoke all sessions for a specific user across all devices."""
        user_sessions_key = self._user_sessions_key(user_id)
        session_ids = await self.redis.smembers(user_sessions_key)
        if not session_ids:
            return 0

        pipeline = self.redis.pipeline()
        for s_id in session_ids:
            pipeline.delete(self._session_key(s_id))
        pipeline.delete(user_sessions_key)
        await pipeline.execute()

        # Update DB
        stmt = (
            update(AuthSession)
            .where(AuthSession.user_id == user_id)
            .values(is_revoked=True, last_active_at=datetime.now(UTC))
        )
        await db.execute(stmt)
        await db.commit()
        return len(session_ids)

    async def list_user_sessions(
        self,
        db: AsyncSession,
        user_id: uuid.UUID,
        current_session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List active sessions for a user."""
        stmt = (
            select(AuthSession)
            .where(
                AuthSession.user_id == user_id,
                AuthSession.is_revoked.is_(False),
                AuthSession.expires_at > datetime.now(UTC),
            )
            .order_by(AuthSession.last_active_at.desc())
        )
        result = await db.execute(stmt)
        sessions = result.scalars().all()

        return [
            {
                "id": s.id,
                "user_id": s.user_id,
                "ip_address": s.ip_address,
                "user_agent": s.user_agent,
                "is_current": s.id == current_session_id,
                "expires_at": s.expires_at,
                "last_active_at": s.last_active_at,
                "created_at": s.created_at,
            }
            for s in sessions
        ]
