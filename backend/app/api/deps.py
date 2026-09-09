"""FastAPI dependencies: DB session, Redis client, cache wrapper, and Zero-Trust Auth guards."""

from collections.abc import AsyncGenerator
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth.principal import Principal
from app.core.auth.verifier import verifier
from app.core.database import get_db
from app.core.redis import CacheClient, get_redis

DbDep = Annotated[AsyncSession, Depends(get_db)]
RedisDep = Annotated[Redis, Depends(get_redis)]


async def get_cache(redis: RedisDep) -> AsyncGenerator[CacheClient, None]:
    yield CacheClient(redis)


CacheDep = Annotated[CacheClient, Depends(get_cache)]


# ── Auth & Zero-Trust Dependencies ──────────────────────────────────────────


async def get_current_user(request: Request) -> Principal:
    """Extract and verify assertion token; requires valid authenticated principal."""
    return await verifier.authenticate_request(request, required=True)


async def get_optional_user(request: Request) -> Principal:
    """Extract and verify assertion token if present; returns anonymous Principal otherwise."""
    return await verifier.authenticate_request(request, required=False)


CurrentUserDep = Annotated[Principal, Depends(get_current_user)]
OptionalUserDep = Annotated[Principal, Depends(get_optional_user)]


def require_roles(*roles: str) -> Any:
    """Factory returning a FastAPI dependency that enforces role requirements (or admin)."""

    async def _role_guard(principal: CurrentUserDep) -> Principal:
        if not principal.has_role(*roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: required role in {list(roles)}",
            )
        return principal

    return Depends(_role_guard)


def require_permissions(*permissions: str) -> Any:
    """Factory returning a FastAPI dependency that enforces permission requirements (or admin)."""

    async def _permission_guard(principal: CurrentUserDep) -> Principal:
        if not principal.has_permission(*permissions):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission denied: required permission in {list(permissions)}",
            )
        return principal

    return Depends(_permission_guard)
