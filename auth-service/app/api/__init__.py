"""API package for Auth Service."""

from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.internal import router as internal_router
from app.api.jwks import router as jwks_router

__all__ = ["auth_router", "internal_router", "jwks_router", "health_router"]
