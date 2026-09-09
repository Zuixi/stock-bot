"""Tests for Settings model-level security validation."""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_production_with_insecure_cookies_rejected() -> None:
    """APP_ENV=production + cookie_secure=False must fail fast at startup."""
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(app_env="production", cookie_secure=False, _env_file=None)


def test_production_with_secure_cookies_accepted() -> None:
    """APP_ENV=production + cookie_secure=True is the only valid production combo."""
    config = Settings(app_env="production", cookie_secure=True, _env_file=None)
    assert config.app_env == "production"
    assert config.cookie_secure is True


def test_development_allows_insecure_cookies() -> None:
    """Non-production environments keep the plain-HTTP friendly default."""
    config = Settings(app_env="development", cookie_secure=False, _env_file=None)
    assert config.cookie_secure is False


def test_internal_api_token_defaults_to_empty() -> None:
    """Empty internal token = /internal/* enforcement disabled (dev/test)."""
    config = Settings(_env_file=None)
    assert config.internal_api_token == ""
