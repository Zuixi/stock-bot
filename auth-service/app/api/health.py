"""Health check endpoints for auth-service."""

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.redis import get_redis

router = APIRouter(tags=["Health"])


@router.get("/health/live", summary="Liveness probe")
async def liveness() -> dict[str, str]:
    """Basic liveness probe verifying HTTP stack is responsive."""
    return {"status": "ok"}


@router.get("/health/ready", summary="Readiness probe")
async def readiness(
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> JSONResponse:
    """Readiness probe checking database and Redis dependencies."""
    db_ok = False
    redis_ok = False

    try:
        await db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        db_ok = False

    try:
        redis_ok = await redis.ping()
    except Exception:
        redis_ok = False

    all_ready = db_ok and redis_ok
    status_code = status.HTTP_200_OK if all_ready else status.HTTP_503_SERVICE_UNAVAILABLE

    return JSONResponse(
        status_code=status_code,
        content={
            "status": "ready" if all_ready else "not_ready",
            "database": db_ok,
            "redis": redis_ok,
        },
    )
