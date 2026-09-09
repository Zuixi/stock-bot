"""FastAPI application factory and middleware entrypoint for Auth Service."""

import logging
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.jwks import router as jwks_router
from app.config import settings
from app.core.redis import close_redis

logger = logging.getLogger("auth_service")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application startup and graceful shutdown lifecycle."""
    logger.info("Starting stock-bot-auth-service...")
    yield
    logger.info("Shutting down stock-bot-auth-service...")
    await close_redis()


def create_app() -> FastAPI:
    """Create and configure FastAPI application instance."""
    app = FastAPI(
        title="Stock Bot Auth Service",
        description="Authentication, RBAC, Sessions, and JWKS Key Distribution Microservice",
        version="0.1.0",
        lifespan=lifespan,
    )

    # 1. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Request Trace ID & Logging Middleware
    @app.middleware("http")
    async def trace_id_middleware(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        trace_id = request.headers.get("X-Request-Id") or f"req-{uuid.uuid4()}"
        request.state.trace_id = trace_id

        response = await call_next(request)
        response.headers["X-Request-Id"] = trace_id
        return response

    # 3. Global Exception Handlers adhering to RFC 7807 unified contract
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4()}")
        detail = exc.detail

        if isinstance(detail, dict):
            code = detail.get("code", "HTTP_ERROR")
            message = detail.get("message", "请求处理失败")
            details = detail.get("details", None)
        else:
            code = "HTTP_ERROR"
            if exc.status_code == status.HTTP_401_UNAUTHORIZED:
                code = "AUTH_UNAUTHORIZED"
            elif exc.status_code == status.HTTP_403_FORBIDDEN:
                code = "AUTH_FORBIDDEN"
            elif exc.status_code == status.HTTP_404_NOT_FOUND:
                code = "RESOURCE_NOT_FOUND"
            elif exc.status_code == status.HTTP_409_CONFLICT:
                code = "RESOURCE_ALREADY_EXISTS"
            elif exc.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                code = "RATE_LIMITED"
            message = str(detail)
            details = None

        return JSONResponse(
            status_code=exc.status_code,
            content={
                "code": code,
                "message": message,
                "details": details,
                "trace_id": trace_id,
            },
            headers={"X-Request-Id": trace_id},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4()}")
        errors = exc.errors()

        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={
                "code": "VALIDATION_ERROR",
                "message": "请求参数校验失败",
                "details": errors,
                "trace_id": trace_id,
            },
            headers={"X-Request-Id": trace_id},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        trace_id = getattr(request.state, "trace_id", f"req-{uuid.uuid4()}")
        logger.error(f"Unhandled error [{trace_id}]: {exc}", exc_info=True)

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "code": "INTERNAL_ERROR",
                "message": "服务内部异常，请稍后重试",
                "details": str(exc) if settings.debug else None,
                "trace_id": trace_id,
            },
            headers={"X-Request-Id": trace_id},
        )

    # 4. Include routers
    app.include_router(health_router)
    app.include_router(jwks_router)
    app.include_router(auth_router)
    app.include_router(internal_router)

    return app


app = create_app()
