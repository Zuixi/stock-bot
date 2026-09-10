"""Async Redis client management for Auth Service."""

from collections.abc import AsyncGenerator

from redis.asyncio import ConnectionPool, Redis

from app.config import settings

_redis_pool: ConnectionPool | None = None
_redis_client: Redis | None = None


def get_redis_pool() -> ConnectionPool:
    """Get or initialize the global Redis connection pool."""
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            max_connections=50,
        )
    return _redis_pool


def get_redis_client() -> Redis:
    """Get or initialize the global Redis client instance."""
    global _redis_client
    if _redis_client is None:
        pool = get_redis_pool()
        _redis_client = Redis(connection_pool=pool)
    return _redis_client


async def close_redis() -> None:
    """Gracefully close Redis client and connection pool."""
    global _redis_client, _redis_pool
    if _redis_client is not None:
        await _redis_client.close()
        _redis_client = None
    if _redis_pool is not None:
        await _redis_pool.disconnect()
        _redis_pool = None


async def get_redis() -> AsyncGenerator[Redis, None]:
    """FastAPI dependency yielding the async Redis client."""
    client = get_redis_client()
    yield client
