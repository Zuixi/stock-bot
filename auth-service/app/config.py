"""Configuration settings for Auth Service."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service info
    app_name: str = "stock-bot-auth-service"
    app_env: str = "development"
    debug: bool = False

    # Database & Redis
    database_url: str = Field(
        default="postgresql+asyncpg://stockbot:stockbot123@localhost:5432/stock_bot_auth",
        description="Async PostgreSQL connection URL",
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL for session storage",
    )

    # Security & Password
    argon2_time_cost: int = 3
    argon2_memory_cost: int = 65536  # 64MB
    argon2_parallelism: int = 4
    argon2_hash_len: int = 32
    argon2_salt_len: int = 16

    # Session & Cookie
    session_ttl: int = 86400  # 24 hours
    session_cookie_name: str = "stockbot_session"
    csrf_cookie_name: str = "stockbot_csrf"
    cookie_secure: bool = False
    cookie_httponly: bool = True
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # Service-to-service token guarding /internal/* endpoints (X-Internal-Token).
    # Empty by default = not enforced (local dev / tests); REQUIRED in production.
    internal_api_token: str = ""

    @field_validator("cookie_secure")
    @classmethod
    def require_secure_cookies_in_production(cls, v: bool, info: ValidationInfo) -> bool:
        """Fail fast on insecure production configs (cookies must ride HTTPS)."""
        app_env = info.data.get("app_env", "development")
        if app_env == "production" and not v:
            raise ValueError(
                "COOKIE_SECURE must be true when APP_ENV=production (HTTPS required); "
                "set AUTH_COOKIE_SECURE=true or deploy behind TLS"
            )
        return v

    # JWT & JWKS Assertion
    # Defaults MUST stay aligned with backend Settings.auth_issuer / auth_audience
    # (see backend/app/config.py) — cross-service Principal Assertion contract.
    jwt_issuer: str = "stock-bot-auth"
    jwt_audience: str = "urn:stock-bot:api"
    jwt_kid: str = "auth-key-2026-01"
    jwt_algorithm: str = "RS256"
    assertion_ttl: int = 60  # 60s short-lived assertion token
    jwt_private_key_pem: str | None = None
    jwt_public_key_pem: str | None = None

    # CORS
    cors_origins: list[str] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://localhost:80",
        "http://localhost:8000",
        "http://localhost:8001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
        "http://127.0.0.1:8001",
    ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
