"""Redis client and cache helpers."""

import json
import logging
from collections.abc import AsyncGenerator
from typing import Any

import redis
import redis.asyncio as aioredis
from redis.asyncio import Redis

from app.config import settings

logger = logging.getLogger(__name__)

_redis_pool: Redis | None = None


async def get_redis_pool() -> Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
        )
    return _redis_pool


async def close_redis_pool() -> None:
    global _redis_pool
    if _redis_pool is not None:
        await _redis_pool.aclose()
        _redis_pool = None


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI dependency: yield the shared Redis client."""
    pool = await get_redis_pool()
    yield pool


class CacheClient:
    """High-level cache operations with JSON serialization.
    All operations are graceful — Redis unavailability is silently ignored.
    """

    def __init__(self, redis: Redis, default_ttl: int = settings.redis_default_ttl) -> None:
        self._redis = redis
        self._default_ttl = default_ttl

    async def get(self, key: str) -> Any | None:
        try:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            return json.loads(raw)
        except (ConnectionError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            logger.warning("Redis unavailable — cache miss for key %s", key)
            return None
        except json.JSONDecodeError:
            logger.warning("Cache value for key %s is not valid JSON", key)
            return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        try:
            await self._redis.set(
                key,
                json.dumps(value, ensure_ascii=False, default=str),
                ex=ttl if ttl is not None else self._default_ttl,
            )
        except (ConnectionError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            logger.warning("Redis unavailable — cache set skipped for key %s", key)

    async def delete(self, *keys: str) -> int:
        try:
            return await self._redis.delete(*keys)
        except (ConnectionError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            return 0

    async def delete_pattern(self, pattern: str) -> int:
        try:
            keys = await self._redis.keys(pattern)
            if not keys:
                return 0
            return await self._redis.delete(*keys)
        except (ConnectionError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            return 0

    async def exists(self, key: str) -> bool:
        try:
            return bool(await self._redis.exists(key))
        except (ConnectionError, redis.exceptions.ConnectionError, redis.exceptions.TimeoutError):
            return False
