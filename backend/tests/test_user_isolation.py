"""Unit & Integration tests for multi-user data isolation (Tags, Watchlists, Tasks, Zero-Trust)."""

import time
import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import jwt
import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI, status
from httpx import ASGITransport, AsyncClient

from app.api.deps import get_cache, get_db
from app.api.v1 import router as api_v1_router
from app.config import settings
from app.core.auth.jwks import jwks_client
from app.core.redis import CacheClient
from app.models.task import Task
from app.models.watchlist import UserWatchlist, UserWatchlistItem
from app.schemas.stock import UserTagOut
from app.schemas.task import FetchUniverseRequest
from app.services import task_service, user_tag_service, watchlist_service

# ── Test RSA Key & Fixtures ─────────────────────────────────────────────────


@pytest.fixture(scope="session")
def rsa_key_pair() -> tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]:
    """Generate an RSA key pair for testing."""
    priv = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    pub = priv.public_key()
    kid = "test-isolation-key-2026"
    return priv, pub, kid


@pytest.fixture(autouse=True)
def setup_test_jwks(rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str]) -> None:
    """Register test RSA public key in global jwks_client."""
    _, pub, kid = rsa_key_pair
    jwks_client.set_static_key(kid, pub)


def make_assertion_jwt(
    priv_key: rsa.RSAPrivateKey,
    kid: str,
    user_id: str,
    username: str,
    roles: list[str] | None = None,
    permissions: list[str] | None = None,
    expires_in: int = 60,
) -> str:
    """Generate a signed Principal Assertion JWT."""
    now = int(time.time())
    payload = {
        "iss": settings.auth_issuer,
        "sub": user_id,
        "aud": settings.auth_audience,
        "username": username,
        "roles": roles if roles is not None else ["researcher"],
        "permissions": (
            permissions
            if permissions is not None
            else ["tasks:trigger", "research:manage"]
        ),
        "iat": now,
        "nbf": now,
        "exp": now + expires_in,
        "jti": f"ast_{uuid.uuid4().hex}",
        "session_id": f"sess_{uuid.uuid4().hex[:8]}",
    }
    return jwt.encode(
        payload,
        priv_key,
        algorithm="RS256",
        headers={"kid": kid, "typ": "JWT"},
    )


# ── Mock Cache & DB Helpers ──────────────────────────────────────────────────


class InMemoryCache(CacheClient):
    """In-memory Redis Cache mock for testing."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        if key in self.store:
            del self.store[key]
            return 1
        return 0

    async def exists(self, key: str) -> bool:
        return key in self.store


# ── 1. Unit Tests: User Tag Isolation ────────────────────────────────────────


@pytest.mark.asyncio
async def test_user_tag_service_isolation() -> None:
    """Test that User A and User B tags are strictly isolated."""
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()

    assert user_tag_service._to_uuid(user_a) == user_a
    assert user_tag_service._to_uuid(str(user_a)) == user_a
    assert user_tag_service._to_uuid(user_b) != user_a


# ── 2. Unit Tests: Watchlist Isolation ──────────────────────────────────────


@pytest.mark.asyncio
async def test_watchlist_service_isolation_and_caching() -> None:
    """Test watchlist listing, adding, and user-isolated caching."""
    cache = InMemoryCache()
    user_a = uuid.uuid4()
    user_b = uuid.uuid4()
    now = datetime.now(UTC)

    mock_db = AsyncMock()

    wl_a = UserWatchlist(
        id=uuid.uuid4(),
        user_id=user_a,
        name="默认自选",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    wl_a.items = [
        UserWatchlistItem(
            id=uuid.uuid4(),
            watchlist_id=wl_a.id,
            symbol="600519",
            exchange="Shanghai_Stocks",
            sort_order=0,
            notes=None,
            created_at=now,
            updated_at=now,
        )
    ]

    wl_b = UserWatchlist(
        id=uuid.uuid4(),
        user_id=user_b,
        name="默认自选",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    wl_b.items = [
        UserWatchlistItem(
            id=uuid.uuid4(),
            watchlist_id=wl_b.id,
            symbol="000001",
            exchange="Shenzen_Stocks",
            sort_order=0,
            notes=None,
            created_at=now,
            updated_at=now,
        )
    ]

    with patch("app.repositories.watchlist_repo.list_user_watchlists") as mock_list_wl:
        mock_list_wl.side_effect = lambda db, uid: [wl_a] if uid == user_a else [wl_b]

        # Fetch User A watchlists
        res_a = await watchlist_service.list_user_watchlists(mock_db, cache, user_a)
        assert len(res_a) == 1
        assert res_a[0].items[0].symbol == "600519"
        # Cache must be user-isolated
        assert f"user:{user_a}:watchlists" in cache.store

        # Fetch User B watchlists
        res_b = await watchlist_service.list_user_watchlists(mock_db, cache, user_b)
        assert len(res_b) == 1
        assert res_b[0].items[0].symbol == "000001"
        assert f"user:{user_b}:watchlists" in cache.store

        # Ensure user A cached content is not affected by user B
        assert cache.store[f"user:{user_a}:watchlists"] != cache.store[f"user:{user_b}:watchlists"]


# ── 3. Unit Tests: Task Trigger requested_by Audit ──────────────────────────


@pytest.mark.asyncio
async def test_task_service_records_requested_by() -> None:
    """Test that task triggers record requested_by user_id."""
    user_id = uuid.uuid4()
    now = datetime.now(UTC)
    mock_db = AsyncMock()

    with patch("app.repositories.task_repo.create_task") as mock_create_task, \
         patch("app.services.task_service.publish_message") as mock_publish:

        mock_task = Task(
            id=uuid.uuid4(),
            type="fetch_universe",
            payload={"exchange": "Shanghai_Stocks"},
            status="pending",
            progress=0,
            result=None,
            error=None,
            started_at=None,
            finished_at=None,
            created_at=now,
            requested_by=user_id,
        )
        mock_create_task.return_value = mock_task

        req = FetchUniverseRequest(exchange="Shanghai_Stocks")
        res = await task_service.trigger_fetch_universe(mock_db, req, requested_by=user_id)

        assert res.requested_by == user_id
        mock_create_task.assert_called_once_with(
            mock_db,
            "fetch_universe",
            {"exchange": "Shanghai_Stocks", "source": "tushare", "include_details": True},
            requested_by=user_id,
        )
        # Verify message payload sent to RabbitMQ has requested_by
        assert mock_publish.call_args[0][1]["requested_by"] == str(user_id)


# ── 4. Integration Tests: HTTP Endpoints & Authorization ───────────────────


@pytest.fixture
def isolation_test_app() -> FastAPI:
    """Create FastAPI test app with v1 router and overridden dependencies."""
    app = FastAPI(title="Isolation Test App")
    app.include_router(api_v1_router)

    mock_db = AsyncMock()
    mock_cache = InMemoryCache()

    async def override_db():
        yield mock_db

    async def override_cache():
        yield mock_cache

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_cache] = override_cache
    return app


@pytest.mark.asyncio
async def test_watchlist_endpoint_unauthorized(isolation_test_app: FastAPI) -> None:
    """Unauthenticated requests to /api/v1/watchlists return 401 Unauthorized."""
    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Missing token
        res = await client.get("/api/v1/watchlists")
        assert res.status_code == status.HTTP_401_UNAUTHORIZED

        # Spoofed unverified header
        res_spoofed = await client.get(
            "/api/v1/watchlists",
            headers={"X-User-Id": "spoofed-user-id"},
        )
        assert res_spoofed.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_watchlist_endpoint_isolation(
    isolation_test_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """User A and User B accessing /api/v1/watchlists receive only their own data."""
    priv, _, kid = rsa_key_pair
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    token_a = make_assertion_jwt(priv, kid, user_id=user_a_id, username="user_a")
    token_b = make_assertion_jwt(priv, kid, user_id=user_b_id, username="user_b")

    wl_a = UserWatchlist(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_a_id),
        name="默认自选",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    wl_a.items = [
        UserWatchlistItem(
            id=uuid.uuid4(),
            watchlist_id=wl_a.id,
            symbol="600519",
            sort_order=0,
            exchange=None,
            notes=None,
            created_at=now,
            updated_at=now,
        )
    ]

    wl_b = UserWatchlist(
        id=uuid.uuid4(),
        user_id=uuid.UUID(user_b_id),
        name="默认自选",
        is_default=True,
        created_at=now,
        updated_at=now,
    )
    wl_b.items = [
        UserWatchlistItem(
            id=uuid.uuid4(),
            watchlist_id=wl_b.id,
            symbol="000001",
            sort_order=0,
            exchange=None,
            notes=None,
            created_at=now,
            updated_at=now,
        )
    ]

    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.repositories.watchlist_repo.list_user_watchlists") as mock_list_wl:
            mock_list_wl.side_effect = lambda db, uid: [wl_a] if str(uid) == user_a_id else [wl_b]

            # User A request
            resp_a = await client.get(
                "/api/v1/watchlists",
                headers={"X-Principal-Assertion": token_a},
            )
            assert resp_a.status_code == status.HTTP_200_OK
            data_a = resp_a.json()
            assert len(data_a) == 1
            assert data_a[0]["user_id"] == user_a_id
            assert data_a[0]["items"][0]["symbol"] == "600519"

            # User B request
            resp_b = await client.get(
                "/api/v1/watchlists",
                headers={"X-Principal-Assertion": token_b},
            )
            assert resp_b.status_code == status.HTTP_200_OK
            data_b = resp_b.json()
            assert len(data_b) == 1
            assert data_b[0]["user_id"] == user_b_id
            assert data_b[0]["items"][0]["symbol"] == "000001"


@pytest.mark.asyncio
async def test_watchlist_item_delete_cross_user_forbidden(
    isolation_test_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """User B cannot delete User A's watchlist item."""
    priv, _, kid = rsa_key_pair
    user_b_id = str(uuid.uuid4())
    token_b = make_assertion_jwt(priv, kid, user_id=user_b_id, username="user_b")

    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.watchlist_service.remove_watchlist_item") as mock_remove:
            # User B does not have 600519 in their watchlist, return False
            mock_remove.return_value = False

            resp = await client.delete(
                "/api/v1/watchlists/items/600519",
                headers={"X-Principal-Assertion": token_b},
            )
            assert resp.status_code == status.HTTP_404_NOT_FOUND
            mock_remove.assert_called_once()
            assert str(mock_remove.call_args[0][2]) == user_b_id


@pytest.mark.asyncio
async def test_user_tags_endpoint_unauthorized(isolation_test_app: FastAPI) -> None:
    """Unauthenticated requests to stock user-tags return 401 Unauthorized."""
    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        res = await client.get("/api/v1/exchanges/Shanghai_Stocks/stocks/600519/user-tags")
        assert res.status_code == status.HTTP_401_UNAUTHORIZED


@pytest.mark.asyncio
async def test_user_tags_endpoint_isolation(
    isolation_test_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """User A and User B custom tags on the same stock are isolated."""
    priv, _, kid = rsa_key_pair
    user_a_id = str(uuid.uuid4())
    user_b_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    token_a = make_assertion_jwt(priv, kid, user_id=user_a_id, username="user_a")
    token_b = make_assertion_jwt(priv, kid, user_id=user_b_id, username="user_b")

    tag_a = UserTagOut(tag_name="价值成长", created_at=now)
    tag_b = UserTagOut(tag_name="周期反转", created_at=now)

    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.user_tag_service.get_stock_tags") as mock_get_tags:
            mock_get_tags.side_effect = (
                lambda db, uid, sym: [tag_a] if str(uid) == user_a_id else [tag_b]
            )

            # User A gets stock tags
            resp_a = await client.get(
                "/api/v1/exchanges/Shanghai_Stocks/stocks/600519/user-tags",
                headers={"X-Principal-Assertion": token_a},
            )
            assert resp_a.status_code == status.HTTP_200_OK
            assert resp_a.json()[0]["tag_name"] == "价值成长"

            # User B gets stock tags
            resp_b = await client.get(
                "/api/v1/exchanges/Shanghai_Stocks/stocks/600519/user-tags",
                headers={"X-Principal-Assertion": token_b},
            )
            assert resp_b.status_code == status.HTTP_200_OK
            assert resp_b.json()[0]["tag_name"] == "周期反转"


@pytest.mark.asyncio
async def test_task_trigger_records_user_id_in_route(
    isolation_test_app: FastAPI,
    rsa_key_pair: tuple[rsa.RSAPrivateKey, rsa.RSAPublicKey, str],
) -> None:
    """Task trigger endpoint passes authenticated user_id into task_service."""
    priv, _, kid = rsa_key_pair
    user_id = str(uuid.uuid4())
    now = datetime.now(UTC)
    token = make_assertion_jwt(
        priv,
        kid,
        user_id=user_id,
        username="trader_bob",
        roles=["trader"],
        permissions=["tasks:trigger"],
    )

    transport = ASGITransport(app=isolation_test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        with patch("app.services.task_service.trigger_fetch_universe") as mock_trigger:
            mock_task_out = Task(
                id=uuid.uuid4(),
                type="fetch_universe",
                status="pending",
                progress=0,
                payload={"exchange": "Shanghai_Stocks"},
                result=None,
                error=None,
                requested_by=uuid.UUID(user_id),
                started_at=None,
                finished_at=None,
                created_at=now,
            )
            mock_trigger.return_value = mock_task_out

            resp = await client.post(
                "/api/v1/tasks/fetch-universe",
                json={"exchange": "Shanghai_Stocks"},
                headers={"X-Principal-Assertion": token},
            )
            assert resp.status_code == status.HTTP_202_ACCEPTED
            assert mock_trigger.call_args[1]["requested_by"] == uuid.UUID(user_id)
