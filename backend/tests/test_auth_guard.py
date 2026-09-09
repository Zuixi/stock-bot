"""Unit tests for Zero-Trust Principal Assertion JWT verification and RBAC guards."""

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import APIRouter, FastAPI, HTTPException, status
from httpx import ASGITransport, AsyncClient

from app.api.deps import (
    CurrentUserDep,
    OptionalUserDep,
    require_permissions,
    require_roles,
)
from app.api.v1 import router as api_v1_router
from app.config import settings
from app.core.auth.jwks import jwks_client
from app.core.auth.principal import Principal
from app.core.auth.verifier import verifier

# ── Test Key & Token Fixtures ───────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]:
    """Generate an RSA key pair for testing."""
    priv = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    pub = priv.public_key()
    kid = "test-auth-key-2026"
    return priv, pub, kid


@pytest.fixture(autouse=True)
def setup_test_jwks(rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]) -> None:
    """Register test RSA public key in global jwks_client."""
    _, pub, kid = rsa_key_pair
    jwks_client.set_static_key(kid, pub)


def make_test_jwt(
    priv_key: rsa.RSAPrivateKey,
    kid: str,
    user_id: str = "user-123",
    username: str = "alice",
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    issuer: str | None = None,
    audience: str | None = None,
    expires_in: int = 60,
    custom_claims: dict[str, Any] | None = None,
) -> str:
    """Sign an assertion JWT with the test RSA private key."""
    now = int(time.time())
    payload = {
        "iss": issuer or settings.auth_issuer,
        "sub": user_id,
        "aud": audience or settings.auth_audience,
        "username": username,
        "roles": roles if roles is not None else ["researcher"],
        "permissions": permissions if permissions is not None else ["research:manage"],
        "iat": now,
        "nbf": now,
        "exp": now + expires_in,
        "jti": f"ast_{uuid.uuid4().hex}",
        "session_id": f"sess_{uuid.uuid4().hex[:8]}",
    }
    if custom_claims:
        payload.update(custom_claims)
    return jwt.encode(
        payload,
        priv_key,
        algorithm="RS256",
        headers={"kid": kid, "typ": "JWT"},
    )


# ── Test Application Setup ──────────────────────────────────────────────────


def create_guard_test_app() -> FastAPI:
    """Build a minimal test application with auth-guarded endpoints."""
    app = FastAPI(title="Auth Guard Test App")
    test_router = APIRouter(prefix="/guard-test")

    @test_router.get("/public")
    async def public_endpoint(user: OptionalUserDep) -> dict[str, Any]:
        return {
            "authenticated": user.is_authenticated,
            "user_id": user.user_id,
            "username": user.username,
        }

    @test_router.get("/me")
    async def me_endpoint(user: CurrentUserDep) -> dict[str, Any]:
        return {
            "user_id": user.user_id,
            "username": user.username,
            "roles": user.roles,
            "permissions": user.permissions,
        }

    @test_router.post(
        "/admin-only",
        dependencies=[require_roles("admin")],
    )
    async def admin_only_endpoint() -> dict[str, str]:
        return {"status": "admin_granted"}

    @test_router.post(
        "/tasks-trigger",
        dependencies=[require_permissions("tasks:trigger")],
    )
    async def tasks_trigger_endpoint() -> dict[str, str]:
        return {"status": "tasks_trigger_granted"}

    @test_router.post(
        "/research-manage",
        dependencies=[require_permissions("research:manage")],
    )
    async def research_manage_endpoint() -> dict[str, str]:
        return {"status": "research_manage_granted"}

    app.include_router(test_router)
    app.include_router(api_v1_router)
    return app


@pytest.fixture
def guard_app() -> FastAPI:
    return create_guard_test_app()


# ── Unit Tests: Principal Model ─────────────────────────────────────────────


def test_principal_model_methods() -> None:
    """Verify Principal role/permission checking and anonymous creation."""
    anon = Principal.anonymous(trace_id="tr-001")
    assert not anon.is_authenticated
    assert anon.user_id is None
    assert anon.trace_id == "tr-001"
    assert not anon.has_role("admin")
    assert not anon.has_role("researcher")
    assert not anon.has_permission("tasks:trigger")

    # Regular researcher
    user = Principal(
        user_id="u1",
        username="bob",
        roles=["researcher"],
        permissions=["research:manage", "research:view"],
        is_authenticated=True,
    )
    assert user.is_authenticated
    assert user.has_role("researcher")
    assert not user.has_role("admin")
    assert user.has_permission("research:manage")
    assert not user.has_permission("tasks:trigger")

    # Admin role has superuser bypass
    admin = Principal(
        user_id="u0",
        username="admin",
        roles=["admin"],
        permissions=[],
        is_authenticated=True,
    )
    assert admin.has_role("anything")
    assert admin.has_permission("tasks:trigger")
    assert admin.has_permission("research:manage")


# ── Unit Tests: Verifier & Zero-Trust ────────────────────────────────────────


@pytest.mark.asyncio
async def test_verifier_valid_token(
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Valid RS256 token generates authenticated Principal."""
    priv, _, kid = rsa_key_pair
    token = make_test_jwt(
        priv,
        kid,
        user_id="usr-888",
        username="carol",
        roles=["analyst", "operator"],
        permissions=["tasks:trigger"],
    )

    principal = await verifier.verify_token(token)
    assert principal.is_authenticated
    assert principal.user_id == "usr-888"
    assert principal.username == "carol"
    assert "analyst" in principal.roles
    assert "tasks:trigger" in principal.permissions


@pytest.mark.asyncio
async def test_verifier_expired_token(
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Expired assertion token raises 401."""
    priv, _, kid = rsa_key_pair
    token = make_test_jwt(priv, kid, expires_in=-10)

    with pytest.raises(Exception) as exc_info:
        await verifier.verify_token(token)
    assert "expired" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_verifier_wrong_signature() -> None:
    """Token signed by untrusted key raises 401."""
    other_priv = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    token = make_test_jwt(other_priv, "unknown-kid")

    with pytest.raises(Exception):
        await verifier.verify_token(token)


@pytest.mark.asyncio
async def test_cross_service_assertion_contract(
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Cross-service contract: auth-service claim structure must verify on backend.

    Builds a JWT by hand with EXACTLY the claim structure produced by
    auth-service KeyManager.sign_assertion (iss="stock-bot-auth",
    aud="urn:stock-bot:api", claims sub/session_id/username/roles/permissions/
    iat/exp/jti, header kid) and verifies it with the backend verifier.
    Tampering with iss or aud must fail.
    """
    priv, pub, kid = rsa_key_pair
    jwks_client.set_static_key(kid, pub)

    now = int(time.time())
    payload = {
        "iss": "stock-bot-auth",
        "sub": "usr_cross_001",
        "aud": "urn:stock-bot:api",
        "session_id": "sess_cross_001",
        "username": "cross_user",
        "roles": ["trader"],
        "permissions": ["stocks:read", "watchlists:write"],
        "iat": now,
        "exp": now + 60,
        "jti": "ast_cross001",
    }
    token = jwt.encode(payload, priv, algorithm="RS256", headers={"kid": kid, "typ": "JWT"})

    principal = await verifier.verify_token(token)
    assert principal.is_authenticated
    assert principal.user_id == "usr_cross_001"
    assert principal.username == "cross_user"
    assert principal.session_id == "sess_cross_001"
    assert principal.roles == ["trader"]
    assert "watchlists:write" in principal.permissions

    # Tampered issuer must be rejected
    bad_issuer = dict(payload, iss="https://evil.example.com")
    tampered = jwt.encode(bad_issuer, priv, algorithm="RS256", headers={"kid": kid})
    with pytest.raises(HTTPException):
        await verifier.verify_token(tampered)

    # Tampered audience must be rejected
    bad_audience = dict(payload, aud="urn:evil:api")
    tampered = jwt.encode(bad_audience, priv, algorithm="RS256", headers={"kid": kid})
    with pytest.raises(HTTPException):
        await verifier.verify_token(tampered)


# ── Integration Tests: HTTP Auth Guards ─────────────────────────────────────


@pytest.mark.asyncio
async def test_public_endpoint_access(
    guard_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Public endpoints work for both anonymous and authenticated callers."""
    priv, _, kid = rsa_key_pair
    transport = ASGITransport(app=guard_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anonymous
        resp = await client.get("/guard-test/public")
        assert resp.status_code == status.HTTP_200_OK
        assert resp.json()["authenticated"] is False

        # Authenticated
        token = make_test_jwt(priv, kid, user_id="u-public", username="public_user")
        resp_auth = await client.get(
            "/guard-test/public",
            headers={"X-Principal-Assertion": token},
        )
        assert resp_auth.status_code == status.HTTP_200_OK
        data = resp_auth.json()
        assert data["authenticated"] is True
        assert data["username"] == "public_user"


@pytest.mark.asyncio
async def test_spoofed_headers_are_ignored(guard_app: FastAPI) -> None:
    """Unsigned X-User-* headers must be ignored (Zero-Trust protection)."""
    transport = ASGITransport(app=guard_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Attacker tries to inject spoofed X-User headers without assertion JWT
        resp = await client.get(
            "/guard-test/me",
            headers={
                "X-User-Id": "spoofed-admin-id",
                "X-User-Role": "admin",
                "X-User-Roles": "admin",
                "X-User-Permissions": "*:*",
            },
        )
        # Must return 401 Unauthorized because assertion JWT is missing
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED

        # Also verify role-protected endpoint rejects spoofed header
        resp_admin = await client.post(
            "/guard-test/admin-only",
            headers={"X-User-Role": "admin"},
        )
        assert resp_admin.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_rbac_roles_guard(
    guard_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Role guards require matching role or admin."""
    priv, _, kid = rsa_key_pair
    transport = ASGITransport(app=guard_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Anonymous -> 401
        res1 = await client.post("/guard-test/admin-only")
        assert res1.status_code == status.HTTP_401_UNAUTHORIZED

        # Researcher role -> 403
        researcher_token = make_test_jwt(priv, kid, roles=["researcher"])
        res2 = await client.post(
            "/guard-test/admin-only",
            headers={"X-Principal-Assertion": researcher_token},
        )
        assert res2.status_code == status.HTTP_403_FORBIDDEN

        # Admin role -> 200
        admin_token = make_test_jwt(priv, kid, roles=["admin"])
        res3 = await client.post(
            "/guard-test/admin-only",
            headers={"X-Principal-Assertion": admin_token},
        )
        assert res3.status_code == status.HTTP_200_OK
        assert res3.json()["status"] == "admin_granted"


@pytest.mark.asyncio
async def test_rbac_permissions_guard(
    guard_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Permission guards require specific permission or admin."""
    priv, _, kid = rsa_key_pair
    transport = ASGITransport(app=guard_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Token with research:manage permission
        research_token = make_test_jwt(
            priv, kid, roles=["researcher"], permissions=["research:manage"]
        )

        # research-manage -> 200
        res_ok = await client.post(
            "/guard-test/research-manage",
            headers={"X-Principal-Assertion": research_token},
        )
        assert res_ok.status_code == status.HTTP_200_OK

        # tasks-trigger with only research:manage -> 403
        res_forbidden = await client.post(
            "/guard-test/tasks-trigger",
            headers={"X-Principal-Assertion": research_token},
        )
        assert res_forbidden.status_code == status.HTTP_403_FORBIDDEN

        # Token with tasks:trigger permission -> 200
        tasks_token = make_test_jwt(priv, kid, roles=["operator"], permissions=["tasks:trigger"])
        res_tasks_ok = await client.post(
            "/guard-test/tasks-trigger",
            headers={"Authorization": f"Bearer {tasks_token}"},
        )
        assert res_tasks_ok.status_code == status.HTTP_200_OK

        # Admin role bypasses permission check -> 200
        admin_token = make_test_jwt(priv, kid, roles=["admin"], permissions=[])
        res_admin_tasks = await client.post(
            "/guard-test/tasks-trigger",
            headers={"X-Principal-Assertion": admin_token},
        )
        assert res_admin_tasks.status_code == status.HTTP_200_OK


@pytest.mark.asyncio
async def test_stock_api_endpoints_protected(
    guard_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Stock API sensitive endpoints (tasks trigger, batch metrics, sse backfill) require auth."""
    priv, _, kid = rsa_key_pair
    transport = ASGITransport(app=guard_app)

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. POST /api/v1/tasks/fetch-universe without auth -> 401
        res1 = await client.post(
            "/api/v1/tasks/fetch-universe",
            json={"exchange": "Shanghai_Stocks"},
        )
        assert res1.status_code == status.HTTP_401_UNAUTHORIZED

        # 2. POST /api/v1/tasks/fetch-universe with unauthorized token -> 403
        viewer_token = make_test_jwt(priv, kid, roles=["viewer"], permissions=["stocks:read"])
        res2 = await client.post(
            "/api/v1/tasks/fetch-universe",
            json={"exchange": "Shanghai_Stocks"},
            headers={"X-Principal-Assertion": viewer_token},
        )
        assert res2.status_code == status.HTTP_403_FORBIDDEN

        # 3. POST /api/v1/industries/sw_pork/metrics/batch without auth -> 401
        res3 = await client.post(
            "/api/v1/industries/sw_pork/metrics/batch",
            json={"items": []},
        )
        assert res3.status_code == status.HTTP_401_UNAUTHORIZED

        # 4. POST /api/v1/industries/sw_pork/metrics/batch with wrong permission -> 403
        tasks_token = make_test_jwt(priv, kid, roles=["operator"], permissions=["tasks:trigger"])
        res4 = await client.post(
            "/api/v1/industries/sw_pork/metrics/batch",
            json={"items": []},
            headers={"X-Principal-Assertion": tasks_token},
        )
        assert res4.status_code == status.HTTP_403_FORBIDDEN

        # 5. POST /api/v1/market/sse-snapshots/backfill without auth -> 401
        res5 = await client.post(
            "/api/v1/market/sse-snapshots/backfill",
            json={"start_date": "2026-01-01", "end_date": "2026-01-02"},
        )
        assert res5.status_code == status.HTTP_401_UNAUTHORIZED
