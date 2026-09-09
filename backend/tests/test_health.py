"""Smoke tests: health check endpoint."""

import pytest
from httpx import AsyncClient

# Hits the real running API (see conftest client fixture) → needs full stack.
pytestmark = pytest.mark.e2e


@pytest.mark.asyncio
async def test_health(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
