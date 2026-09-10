"""Contract lock: the public market endpoints must emit exactly the key set that the
committed frontend e2e fixtures encode.

The homepage e2e mocks read the same fixture files, so without this test a backend
field rename would keep backend unit tests, ``tsc`` and mocked e2e all green while the
UI rendered ``undefined``/``--`` against the live API. This test closes that drift.

Marked ``e2e`` (needs the real DB + FastAPI app). Default addopts exclude it; run
container-side (host has no DB route), e.g.::

    uv run pytest tests/test_market_contract.py -v -m e2e

Fixture resolution is **lazy** and skips when the frontend tree is absent: pytest
imports a module to read its ``pytestmark`` before ``-m`` deselection applies, so a
module-level path lookup that raised would turn a backend-only ``uv run pytest`` into a
collection error.
"""

import json
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.core.database import engine
from app.core.redis import close_redis_pool
from app.main import app

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
async def _dispose_engine_after_test() -> AsyncGenerator[None, None]:
    """Each test gets a fresh event loop (function-scoped), so drop pooled
    connections in the loop that created them — otherwise the next test reuses
    a connection (DB *or* the module-level Redis pool) bound to the closed loop
    ("attached to a different loop")."""
    yield
    await engine.dispose()
    await close_redis_pool()


def _fixtures_dir() -> Path | None:
    """Locate ``frontend/e2e/fixtures`` above this file, or ``None`` if absent.

    Resolved at test time (not import time) so a backend-only checkout still imports
    and collects this module cleanly.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "frontend" / "e2e" / "fixtures"
        if candidate.is_dir():
            return candidate
    return None


def _load(name: str) -> dict[str, Any]:
    fixtures_dir = _fixtures_dir()
    if fixtures_dir is None:
        pytest.skip("frontend/e2e/fixtures not present (backend-only checkout)")
    path = fixtures_dir / name
    if not path.is_file():
        pytest.skip(f"fixture {name} not present (backend-only checkout)")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.asyncio
async def test_rankings_emits_fixture_keyset() -> None:
    fixture = _load("rankings.sample.json")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        for rank_type, sample in fixture.items():
            resp = await client.get(
                "/api/v1/market/rankings", params={"type": rank_type, "limit": 3}
            )
            assert resp.status_code == 200, (rank_type, resp.status_code)
            body = resp.json()
            assert set(body) == set(sample), f"response keys drifted for {rank_type}"
            assert body["items"], f"fixture produced no rows for {rank_type}"
            assert set(body["items"][0]) == set(
                sample["items"][0]
            ), f"item keys drifted for {rank_type}"


@pytest.mark.asyncio
async def test_sw_performance_emits_fixture_keyset() -> None:
    fixture = _load("swPerformance.sample.json")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(
            "/api/v1/market/sw-industry/performance", params={"limit": 3}
        )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == set(fixture)
    assert body["items"], "fixture produced no SW L1 rows"
    assert set(body["items"][0]) == set(fixture["items"][0])
