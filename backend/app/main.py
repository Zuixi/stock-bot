"""FastAPI application factory and lifecycle management."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import router as api_v1_router
from app.config import settings
from app.core.mq import close_mq_connection, get_mq_channel
from app.core.redis import close_redis_pool, get_redis_pool

logging.basicConfig(
    level=logging.DEBUG if settings.app_debug else logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Stock Bot API",
        description="A-share stock universe, quotes, features, and clustering.",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_v1_router)

    @app.on_event("startup")
    async def startup() -> None:
        try:
            await get_redis_pool()
            logger.info("Redis connected")
        except Exception as e:
            logger.warning("Redis unavailable — caching disabled: %s", e)

        try:
            await get_mq_channel()
            logger.info("RabbitMQ connected")
        except Exception as e:
            logger.warning("RabbitMQ unavailable — async tasks will queue locally: %s", e)

        logger.info("Stock Bot API started (env=%s)", settings.app_env)

    @app.on_event("shutdown")
    async def shutdown() -> None:
        await close_redis_pool()
        await close_mq_connection()
        logger.info("Stock Bot API stopped")

    @app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok", "env": settings.app_env}

    return app


app = create_app()
