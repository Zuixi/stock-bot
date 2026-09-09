"""Integration tests for JWKS distribution endpoints."""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_jwks_endpoints(client: AsyncClient) -> None:
    """Test /.well-known/jwks.json and /auth/jwks.json return public keys."""
    resp1 = await client.get("/.well-known/jwks.json")
    assert resp1.status_code == 200
    jwks1 = resp1.json()
    assert "keys" in jwks1
    assert len(jwks1["keys"]) >= 1
    assert jwks1["keys"][0]["kty"] == "RSA"
    assert jwks1["keys"][0]["alg"] == "RS256"

    resp2 = await client.get("/auth/jwks.json")
    assert resp2.status_code == 200
    jwks2 = resp2.json()
    assert jwks2 == jwks1
