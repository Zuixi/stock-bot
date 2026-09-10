"""Principal Assertion token verifier and zero-trust header sanitizer."""

import logging
from typing import Any

import jwt
from fastapi import HTTPException, Request, status

from app.config import settings
from app.core.auth.jwks import JwksClient, jwks_client
from app.core.auth.principal import Principal

logger = logging.getLogger(__name__)

ASSERTION_HEADER = "X-Principal-Assertion"
AUTHORIZATION_HEADER = "Authorization"
BEARER_PREFIX = "Bearer "


class AssertionVerifier:
    """Verifies RS256 signed Principal Assertion tokens against JWKS keys."""

    def __init__(
        self,
        client: JwksClient | None = None,
        issuer: str | None = None,
        audience: str | None = None,
        leeway: int | None = None,
    ) -> None:
        self.jwks = client or jwks_client
        self._issuer = issuer
        self._audience = audience
        self._leeway = leeway

    @property
    def issuer(self) -> str:
        return self._issuer if self._issuer is not None else settings.auth_issuer

    @property
    def audience(self) -> str:
        return self._audience if self._audience is not None else settings.auth_audience

    @property
    def leeway(self) -> int:
        return self._leeway if self._leeway is not None else settings.auth_leeway

    def extract_token(self, request: Request) -> str | None:
        """Extract signed assertion token from request headers.

        Prioritizes X-Principal-Assertion, falls back to Authorization: Bearer <token>.
        Never trusts unverified X-User-* headers.
        """
        raw_assertion = request.headers.get(ASSERTION_HEADER)
        if raw_assertion:
            return raw_assertion.strip()

        auth_header = request.headers.get(AUTHORIZATION_HEADER)
        if auth_header and auth_header.startswith(BEARER_PREFIX):
            return auth_header[len(BEARER_PREFIX) :].strip()

        return None

    async def verify_token(self, token: str, trace_id: str | None = None) -> Principal:
        """Verify token signature, claims, and build an authenticated Principal."""
        try:
            key = await self.jwks.get_key_for_token(token)
        except Exception as exc:
            logger.warning("JWKS key resolution failed: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid assertion token: key resolution error ({exc})",
            ) from exc

        # Acceptable issuers and audiences for robust gateway/local compatibility
        allowed_issuers = list({self.issuer, "stock-auth-service", "stock-bot-auth"})
        allowed_audiences = list({self.audience, "stock-api", "urn:stock-bot:api"})

        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                issuer=allowed_issuers,
                audience=allowed_audiences,
                leeway=self.leeway,
                options={
                    "verify_signature": True,
                    "verify_exp": True,
                    "verify_nbf": True,
                    "verify_iss": True,
                    "verify_aud": True,
                    "require": ["exp", "iss", "aud", "sub"],
                },
            )
        except jwt.ExpiredSignatureError as exc:
            logger.warning("Assertion token expired: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Assertion token expired",
            ) from exc
        except jwt.InvalidTokenError as exc:
            logger.warning("Assertion token invalid: %s", exc)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid assertion token: {exc}",
            ) from exc

        sub = str(payload.get("sub", ""))
        if not sub:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid assertion token: missing 'sub' claim",
            )

        roles = payload.get("roles") or []
        if isinstance(roles, str):
            roles = [r.strip() for r in roles.split(",") if r.strip()]

        permissions = payload.get("permissions") or []
        if isinstance(permissions, str):
            permissions = [p.strip() for p in permissions.split(",") if p.strip()]

        token_trace_id = trace_id or payload.get("jti") or payload.get("trace_id")

        return Principal(
            user_id=sub,
            username=payload.get("username", sub),
            email=payload.get("email"),
            roles=list(roles),
            permissions=list(permissions),
            trace_id=token_trace_id,
            session_id=payload.get("session_id"),
            is_authenticated=True,
        )

    async def authenticate_request(self, request: Request, required: bool = True) -> Principal:
        """Authenticate an incoming HTTP request using Zero-Trust principles.

        Any unverified X-User-* headers passed by the client are strictly ignored.
        """
        trace_id = request.headers.get("X-Request-Id") or request.headers.get("X-Trace-Id")

        if not settings.auth_enabled:
            # When authentication is globally disabled (e.g. legacy/testing)
            return Principal(
                user_id="anonymous-admin",
                username="admin",
                roles=["admin"],
                permissions=["*:*"],
                trace_id=trace_id,
                is_authenticated=True,
            )

        token = self.extract_token(request)
        if not token:
            if required:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Authentication credentials required",
                )
            return Principal.anonymous(trace_id=trace_id)

        return await self.verify_token(token, trace_id=trace_id)


# Global verifier singleton
verifier = AssertionVerifier()
