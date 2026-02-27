"""FastAPI dependencies: DB session, Redis client, cache wrapper."""

from collections.abc import AsyncGenerator
from typing import Annotated

from fastapi import Depends
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import CacheClient, get_redis

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


async def get_cache(redis: RedisDep) -> AsyncGenerator[CacheClient, None]:
    yield CacheClient(redis)


CacheDep = Annotated[CacheClient, Depends(get_cache)]
