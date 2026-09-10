"""Asynchronous JWKS key loader and local public key cache."""

import asyncio
import logging
import time
from typing import Any

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.config import settings

logger = logging.getLogger(__name__)


class JwksError(Exception):
    """Base exception for JWKS operations."""


class JwksFetchError(JwksError):
    """Raised when fetching JWKS from remote endpoint fails."""


class KeyNotFoundError(JwksError):
    """Raised when the requested key ID is not found in JWKS."""


class JwksClient:
    """Asynchronous JWKS key manager with local caching and single-flight refresh."""

    def __init__(
        self,
        jwks_url: str | None = None,
        cache_ttl: int | None = None,
        static_public_key_pem: str | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._cache_ttl = cache_ttl
        self._static_public_key_pem = static_public_key_pem
        self._keys: dict[str, Any] = {}
        self._last_fetched_at: float = 0.0
        self._lock = asyncio.Lock()
        self._min_refresh_interval: float = 2.0

    @property
    def jwks_url(self) -> str:
        return self._jwks_url if self._jwks_url is not None else settings.auth_jwks_url

    @property
    def cache_ttl(self) -> int:
        return self._cache_ttl if self._cache_ttl is not None else settings.auth_jwks_cache_ttl

    @property
    def static_public_key_pem(self) -> str | None:
        return (
            self._static_public_key_pem
            if self._static_public_key_pem is not None
            else settings.auth_public_key_pem
        )

    def set_static_key(self, kid: str, public_key: Any) -> None:
        """Register a static public key (useful for unit tests or offline environments)."""
        self._keys[kid] = public_key

    def clear_cache(self) -> None:
        """Clear cached JWKS keys."""
        self._keys.clear()
        self._last_fetched_at = 0.0

    async def fetch_keys(self, force: bool = False) -> dict[str, Any]:
        """Fetch keys from remote JWKS URL with single-flight concurrency lock."""
        now = time.time()
        # Fast path if cache is fresh
        if not force and self._keys and (now - self._last_fetched_at < self.cache_ttl):
            return self._keys

        async with self._lock:
            now = time.time()
            if not force and self._keys and (now - self._last_fetched_at < self.cache_ttl):
                return self._keys
            if force and (now - self._last_fetched_at < self._min_refresh_interval) and self._keys:
                return self._keys

            target_url = self.jwks_url
            if not target_url:
                if self.static_public_key_pem:
                    return self._keys
                raise JwksFetchError("AUTH_JWKS_URL is not configured")

            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.get(target_url)
                    resp.raise_for_status()
                    data = resp.json()

                new_keys: dict[str, Any] = {}
                for key_data in data.get("keys", []):
                    kid = key_data.get("kid")
                    if kid:
                        try:
                            pub_key = RSAAlgorithm.from_jwk(key_data)
                            new_keys[kid] = pub_key
                        except Exception as e:
                            logger.warning("Failed to parse JWK kid=%s: %s", kid, e)

                if new_keys:
                    self._keys = new_keys
                    self._last_fetched_at = time.time()
                    logger.info(
                        "Successfully loaded %d JWKS public keys from %s",
                        len(new_keys),
                        target_url,
                    )
                return self._keys
            except Exception as e:
                logger.error("Failed to fetch JWKS from %s: %s", target_url, e)
                if self._keys:
                    return self._keys
                raise JwksFetchError(f"Failed to fetch JWKS from {target_url}: {e}") from e

    async def get_key_for_token(self, token: str) -> Any:
        """Extract key ID from unverified token header and retrieve corresponding public key."""
        try:
            unverified_headers = jwt.get_unverified_header(token)
        except Exception as exc:
            raise JwksError(f"Invalid JWT header format: {exc}") from exc

        kid = unverified_headers.get("kid")

        # Static PEM override (if configured)
        if self.static_public_key_pem:
            return self.static_public_key_pem

        # 1. Lookup in current cache
        if kid and kid in self._keys:
            return self._keys[kid]

        # 2. Unknown kid or cold cache: single-flight refresh
        await self.fetch_keys(force=True)

        if kid and kid in self._keys:
            return self._keys[kid]

        # Fallback if only 1 key exists and kid is omitted
        if not kid and len(self._keys) == 1:
            return next(iter(self._keys.values()))

        raise KeyNotFoundError(f"Public key for kid='{kid}' not found in JWKS")


# Global JWKS client singleton
jwks_client = JwksClient()
