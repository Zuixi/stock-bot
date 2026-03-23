"""
Pytest fixtures — tests hit the real running API via HTTP.
Tests connect to the API's DATABASE_URL pointing to stock_bot_test,
so all queries run against the isolated test database.
"""

import pytest
import pytest_asyncio
from httpx import AsyncClient

import os

# Point to the real test database (same as what the running API uses)
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://stock_user:stock_pass@localhost:5432/stock_bot_test"
)

# API runs inside Docker; use host.docker.internal to reach host port 8000
API_BASE_URL = os.environ.get("API_BASE_URL", "http://host.docker.internal:8000")


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    """
    httpx AsyncClient that hits the real HTTP API.
    No ASGI transport, no event-loop conflicts with the DB driver.
    """
    async with AsyncClient(base_url=API_BASE_URL, timeout=30.0) as ac:
        yield ac
