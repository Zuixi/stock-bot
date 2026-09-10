"""Configuration for forward-auth sidecar (env-driven)."""

import os
from dataclasses import dataclass


@dataclass
class Settings:
    """Runtime settings resolved from environment variables.

    Attributes are intentionally mutable so tests can monkeypatch them.
    """

    auth_service_url: str = "http://auth-service:8001"
    # Empty by default = do NOT send the X-Internal-Token header (dev mode).
    internal_api_token: str = ""
    assertion_cache_ttl: int = 25  # seconds; assertion TTL is 60s, cache stays well below
    session_cookie_name: str = "stockbot_session"
    http_timeout_seconds: float = 5.0


def _load_settings() -> Settings:
    return Settings(
        auth_service_url=os.getenv("AUTH_SERVICE_URL", "http://auth-service:8001"),
        internal_api_token=os.getenv("INTERNAL_API_TOKEN", ""),
        assertion_cache_ttl=int(os.getenv("ASSERTION_CACHE_TTL", "25")),
        session_cookie_name=os.getenv("SESSION_COOKIE_NAME", "stockbot_session"),
        http_timeout_seconds=float(os.getenv("AUTH_HTTP_TIMEOUT", "5")),
    )


settings = _load_settings()
