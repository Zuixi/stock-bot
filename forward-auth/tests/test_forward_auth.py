"""Offline tests for the forward-auth sidecar /verify flow (auth-service mocked)."""

import json
from collections.abc import AsyncGenerator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import settings
from app.main import create_app

AUTH_BASE = "http://auth.test"
VALID_SESSION = "sess_valid"
INVALID_SESSION = "sess_expired"
GOOD_CSRF = "good-csrf"


def _default_handler(request: httpx.Request) -> httpx.Response:
    """Mock auth-service internal API."""
    if request.url.path == "/internal/principal/assertion":
        body = _json_body(request)
        if body.get("session_id") == VALID_SESSION:
            return httpx.Response(
                200,
                json={"assertion_token": f"assertion-for-{VALID_SESSION}", "expires_in": 60},
            )
        return httpx.Response(401, json={"code": "AUTH_UNAUTHORIZED"})
    if request.url.path == "/internal/session/introspect":
        body = _json_body(request)
        return httpx.Response(
            200,
            json={"active": True, "csrf_valid": body.get("csrf_token") == GOOD_CSRF},
        )
    return httpx.Response(404, json={})


def _json_body(request: httpx.Request) -> dict[str, Any]:
    return json.loads(request.content.decode("utf-8"))


def _build_app(
    monkeypatch: pytest.MonkeyPatch,
    calls: list[httpx.Request],
    handler: Callable[[httpx.Request], httpx.Response] = _default_handler,
) -> FastAPI:
    """Build a forward-auth app whose auth-service calls go through MockTransport."""

    def recording_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    monkeypatch.setattr(settings, "auth_service_url", AUTH_BASE)
    monkeypatch.setattr(settings, "internal_api_token", "internal-secret")
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(recording_handler), base_url=AUTH_BASE
    )
    return create_app(http_client=http_client)


def assertion_calls(calls: list[httpx.Request]) -> list[httpx.Request]:
    return [c for c in calls if c.url.path == "/internal/principal/assertion"]


@pytest.fixture
def calls() -> list[httpx.Request]:
    return []


@pytest.fixture
def app_with_mock(calls: list[httpx.Request], monkeypatch: pytest.MonkeyPatch) -> FastAPI:
    return _build_app(monkeypatch, calls)


@pytest.fixture
async def gateway_client(app_with_mock: FastAPI) -> AsyncGenerator[AsyncClient, None]:
    transport = ASGITransport(app=app_with_mock)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        yield ac


# ── Tests ────────────────────────────────────────────────────────────────────


async def test_no_cookie_anonymous_passthrough(
    gateway_client: AsyncClient, calls: list[httpx.Request]
) -> None:
    """Requests without a session cookie pass through untouched (no auth calls)."""
    resp = await gateway_client.get("/verify")
    assert resp.status_code == 200
    assert "x-principal-assertion" not in resp.headers
    assert calls == []


async def test_session_gets_assertion_injected(
    gateway_client: AsyncClient, calls: list[httpx.Request]
) -> None:
    """Valid session cookie -> 200 with X-Principal-Assertion header set.

    Traefik issues the forward-auth subrequest as GET; read requests never touch
    the introspect (CSRF) endpoint.
    """
    resp = await gateway_client.get(
        "/verify", headers={"Cookie": f"stockbot_session={VALID_SESSION}"}
    )
    assert resp.status_code == 200
    assert resp.headers["x-principal-assertion"] == f"assertion-for-{VALID_SESSION}"
    assert len(assertion_calls(calls)) == 1, "auth-service must have been contacted once"
    assert assertion_calls(calls)[0].headers["x-internal-token"] == "internal-secret"
    assert all(c.url.path != "/internal/session/introspect" for c in calls)


async def test_csrf_failure_returns_403(gateway_client: AsyncClient) -> None:
    """Original POST with wrong CSRF token -> 403 AUTH_CSRF_FAILED, trace_id forwarded."""
    resp = await gateway_client.get(
        "/verify",
        headers={
            "Cookie": f"stockbot_session={VALID_SESSION}",
            "X-Forwarded-Method": "POST",
            "X-CSRF-Token": "wrong-csrf",
            "X-Request-Id": "req-fixed-123",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["code"] == "AUTH_CSRF_FAILED"
    assert body["message"] == "CSRF 校验失败"
    assert body["details"] is None
    assert body["trace_id"] == "req-fixed-123"
    assert resp.headers["x-request-id"] == "req-fixed-123"


async def test_csrf_valid_write_passes(gateway_client: AsyncClient) -> None:
    """Original POST with valid CSRF token -> 200 with assertion header."""
    resp = await gateway_client.get(
        "/verify",
        headers={
            "Cookie": f"stockbot_session={VALID_SESSION}",
            "X-Forwarded-Method": "POST",
            "X-CSRF-Token": GOOD_CSRF,
        },
    )
    assert resp.status_code == 200
    assert "x-principal-assertion" in resp.headers


async def test_get_requests_skip_csrf(
    gateway_client: AsyncClient, calls: list[httpx.Request]
) -> None:
    """Original GET never calls the introspect endpoint."""
    resp = await gateway_client.get(
        "/verify",
        headers={
            "Cookie": f"stockbot_session={VALID_SESSION}",
            "X-Forwarded-Method": "GET",
        },
    )
    assert resp.status_code == 200
    assert "x-principal-assertion" in resp.headers
    assert all(c.url.path != "/internal/session/introspect" for c in calls)


async def test_post_subrequest_also_accepted(gateway_client: AsyncClient) -> None:
    """/verify must answer POST too (in case Traefik preserves the original method
    in future versions); without X-Forwarded-Method the actual method decides."""
    resp = await gateway_client.post(
        "/verify", headers={"Cookie": f"stockbot_session={VALID_SESSION}"}
    )
    assert resp.status_code == 403  # write method without CSRF token


async def test_auth_service_down_fails_closed_503(monkeypatch: pytest.MonkeyPatch) -> None:
    """auth-service connection error -> fail closed 503 for session-bearing request."""

    def down_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    calls: list[httpx.Request] = []
    app = _build_app(monkeypatch, calls, handler=down_handler)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        resp = await ac.get(
            "/verify",
            headers={"Cookie": f"stockbot_session={VALID_SESSION}", "X-Request-Id": "req-x"},
        )
    assert resp.status_code == 503
    body = resp.json()
    assert body["code"] == "UPSTREAM_UNAVAILABLE"
    assert body["trace_id"] == "req-x"

    # Anonymous requests still pass through even when auth-service is down.
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://gateway") as ac:
        anon = await ac.get("/verify")
    assert anon.status_code == 200


async def test_invalid_session_fails_closed_503(
    monkeypatch: pytest.MonkeyPatch, calls: list[httpx.Request]
) -> None:
    """auth-service non-200 (e.g. expired session) -> fail closed 503."""
    app = _build_app(monkeypatch, calls)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        resp = await ac.get("/verify", headers={"Cookie": f"stockbot_session={INVALID_SESSION}"})
    assert resp.status_code == 503
    assert resp.json()["code"] == "UPSTREAM_UNAVAILABLE"


async def test_assertion_cache_hit_skips_second_fetch(
    monkeypatch: pytest.MonkeyPatch, calls: list[httpx.Request]
) -> None:
    """Second request for the same session must be served from cache (no 2nd call)."""
    app = _build_app(monkeypatch, calls)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        first = await ac.get("/verify", headers={"Cookie": f"stockbot_session={VALID_SESSION}"})
        second = await ac.get("/verify", headers={"Cookie": f"stockbot_session={VALID_SESSION}"})

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["x-principal-assertion"] == second.headers["x-principal-assertion"]
    assert len(assertion_calls(calls)) == 1, "cache hit must not re-fetch assertion"


async def test_different_sessions_not_shared(
    monkeypatch: pytest.MonkeyPatch, calls: list[httpx.Request]
) -> None:
    """Cache keys are per-session: another session triggers its own fetch."""
    app = _build_app(monkeypatch, calls)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        await ac.get("/verify", headers={"Cookie": f"stockbot_session={VALID_SESSION}"})
        await ac.get("/verify", headers={"Cookie": "stockbot_session=sess_other"})

    assert len(assertion_calls(calls)) == 2


async def test_healthz(monkeypatch: pytest.MonkeyPatch, calls: list[httpx.Request]) -> None:
    """GET /healthz returns ok for the compose healthcheck."""
    app = _build_app(monkeypatch, calls)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://gateway") as ac:
        resp = await ac.get("/healthz")
    assert resp.status_code == 200
    assert resp.text == "ok"
