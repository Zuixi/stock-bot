"""Traefik forward-auth sidecar: session cookie -> Principal Assertion injection + CSRF.

Flow for ``POST /verify`` (invoked by Traefik forwardAuth on every /api request):

1. Parse ``stockbot_session`` from the Cookie header. No session -> anonymous
   passthrough (200, no assertion header injected, auth-service not contacted).
2. Session present -> resolve a short-lived Principal Assertion JWT from
   auth-service (``POST /internal/principal/assertion``) via an in-memory TTL
   cache protected by a single-flight lock (prevents cache stampede).
   Non-200 or connection error -> fail closed with 503.
3. Non-idempotent methods (POST/PUT/PATCH/DELETE, read from
   ``X-Forwarded-Method`` with fallback to the actual request method) must pass
   CSRF enforcement via auth-service ``POST /internal/session/introspect``;
   ``csrf_valid`` not true -> 403 ``AUTH_CSRF_FAILED``.
4. Success -> 200 with ``X-Principal-Assertion`` response header, which Traefik
   copies onto the upstream request via ``authResponseHeaders``.

Client-forged ``X-Principal-Assertion`` headers never reach this point on wired
routes: the ``strip-assertion`` middleware (placed before forward-auth) removes
them at the gateway edge.
"""

import asyncio
import time
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from app.config import settings

# ── Constants ────────────────────────────────────────────────────────────────

CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ASSERTION_HEADER = "X-Principal-Assertion"
CSRF_HEADER = "X-CSRF-Token"
FORWARDED_METHOD_HEADER = "X-Forwarded-Method"
REQUEST_ID_HEADER = "X-Request-Id"
INTERNAL_TOKEN_HEADER = "X-Internal-Token"


class UpstreamAuthError(Exception):
    """auth-service returned a non-200 response for an internal call."""


class AssertionCache:
    """In-memory TTL cache for session assertions with single-flight locking.

    A global asyncio lock is held across cache lookup AND fetch, so concurrent
    misses on the same session (or across sessions) can never stampede
    auth-service: later callers re-check the cache after acquiring the lock and
    hit the entry written by the first fetch. At this sidecar's scale the
    serialization cost is negligible compared to the correctness win.
    """

    def __init__(self, ttl_seconds: int) -> None:
        self.ttl_seconds = ttl_seconds
        self._entries: dict[str, tuple[str, float]] = {}  # session_id -> (token, expires_at)
        self._lock = asyncio.Lock()

    async def get_or_fetch(self, session_id: str, fetch: Callable[[], Awaitable[str]]) -> str:
        """Return a cached assertion or fetch it exactly once under the lock."""
        async with self._lock:
            entry = self._entries.get(session_id)
            now = time.monotonic()
            if entry is not None and entry[1] > now:
                return entry[0]
            token = await fetch()
            self._entries[session_id] = (token, time.monotonic() + self.ttl_seconds)
            return token

    def invalidate(self, session_id: str) -> None:
        """Drop a cached assertion (e.g. session revoked)."""
        self._entries.pop(session_id, None)


# ── Helpers ──────────────────────────────────────────────────────────────────


def parse_session_cookie(cookie_header: str | None, cookie_name: str) -> str | None:
    """Extract a cookie value from a raw Cookie header (None if absent/empty)."""
    if not cookie_header:
        return None
    for part in cookie_header.split(";"):
        name, _, value = part.strip().partition("=")
        if name == cookie_name:
            return value or None
    return None


def _internal_headers() -> dict[str, str]:
    """Headers for auth-service internal calls (token omitted when unconfigured)."""
    headers: dict[str, str] = {}
    if settings.internal_api_token:
        headers[INTERNAL_TOKEN_HEADER] = settings.internal_api_token
    return headers


async def _fetch_assertion(client: httpx.AsyncClient, session_id: str) -> str:
    """Resolve a fresh Principal Assertion token for the given session."""
    resp = await client.post(
        f"{settings.auth_service_url}/internal/principal/assertion",
        json={"session_id": session_id},
        headers=_internal_headers(),
    )
    if resp.status_code != 200:
        raise UpstreamAuthError(f"assertion endpoint returned {resp.status_code}")
    token = resp.json().get("assertion_token")
    if not token:
        raise UpstreamAuthError("assertion response missing 'assertion_token'")
    return token


async def _check_csrf(client: httpx.AsyncClient, session_id: str, csrf_token: str | None) -> bool:
    """Validate the X-CSRF-Token against the session via auth-service introspection."""
    resp = await client.post(
        f"{settings.auth_service_url}/internal/session/introspect",
        json={"session_id": session_id, "csrf_token": csrf_token},
        headers=_internal_headers(),
    )
    if resp.status_code != 200:
        raise UpstreamAuthError(f"introspect endpoint returned {resp.status_code}")
    return resp.json().get("csrf_valid") is True


def _error_response(status_code: int, code: str, message: str, trace_id: str) -> JSONResponse:
    """Unified structured error response (RFC 7807-style contract)."""
    return JSONResponse(
        status_code=status_code,
        content={"code": code, "message": message, "details": None, "trace_id": trace_id},
        headers={REQUEST_ID_HEADER: trace_id},
    )


# ── App factory ──────────────────────────────────────────────────────────────


def create_app(http_client: httpx.AsyncClient | None = None) -> FastAPI:
    """Create the forward-auth FastAPI app.

    ``http_client`` lets tests inject an ``httpx.AsyncClient`` backed by a
    ``MockTransport``; in production the lifespan hook builds the real client.
    """
    # When a client is injected externally the app must not close it on shutdown.
    app_state_http = http_client
    app_state_owns_http_client = http_client is None
    assertion_cache = AssertionCache(settings.assertion_cache_ttl)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        # 生产路径 http_client=None 时 state 上没有 http 属性，必须用 getattr 兜底
        if getattr(app.state, "http", None) is None:
            app.state.http = app_state_http or httpx.AsyncClient(
                timeout=settings.http_timeout_seconds
            )
        yield
        if app.state.owns_http_client and getattr(app.state, "http", None) is not None:
            await app.state.http.aclose()
            app.state.http = None

    app = FastAPI(
        title="stock-bot-forward-auth",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.owns_http_client = app_state_owns_http_client
    app.state.assertion_cache = assertion_cache
    if app_state_http is not None:
        app.state.http = app_state_http

    @app.get("/healthz")
    async def healthz() -> PlainTextResponse:
        """Liveness probe for the compose healthcheck."""
        return PlainTextResponse("ok")

    @app.api_route("/verify", methods=["GET", "POST"])
    async def verify(request: Request) -> Response:
        """Traefik forwardAuth entrypoint: decide + enrich the upstream request.

        Traefik always issues this subrequest as GET (v3 hardcodes MethodGet) and
        reports the ORIGINAL request method via X-Forwarded-Method, so both are
        accepted here.
        """
        trace_id = request.headers.get(REQUEST_ID_HEADER) or f"req-{uuid.uuid4()}"
        client: httpx.AsyncClient = request.app.state.http

        session_id = parse_session_cookie(
            request.headers.get("cookie"), settings.session_cookie_name
        )
        if not session_id:
            # Anonymous passthrough: no cookie, no assertion, no auth-service call.
            return Response(status_code=200)

        cache: AssertionCache = request.app.state.assertion_cache
        try:
            assertion = await cache.get_or_fetch(
                session_id,
                lambda: _fetch_assertion(client, session_id),
            )
        except (httpx.TransportError, UpstreamAuthError):
            # Fail closed: a session-bearing request must never proceed unsigned.
            return _error_response(
                503,
                "UPSTREAM_UNAVAILABLE",
                "认证服务暂不可用，请稍后重试",
                trace_id,
            )

        method = (request.headers.get(FORWARDED_METHOD_HEADER) or request.method).upper()
        if method in CSRF_PROTECTED_METHODS:
            try:
                csrf_valid = await _check_csrf(client, session_id, request.headers.get(CSRF_HEADER))
            except (httpx.TransportError, UpstreamAuthError):
                return _error_response(
                    503,
                    "UPSTREAM_UNAVAILABLE",
                    "认证服务暂不可用，请稍后重试",
                    trace_id,
                )
            if not csrf_valid:
                return _error_response(
                    403,
                    "AUTH_CSRF_FAILED",
                    "CSRF 校验失败",
                    trace_id,
                )

        return Response(
            status_code=200,
            headers={ASSERTION_HEADER: assertion, REQUEST_ID_HEADER: trace_id},
        )

    return app


app = create_app()
