"""Contract lock: the public market endpoints must emit exactly the key set that the
committed frontend e2e fixtures encode.

The homepage e2e mocks read the same fixture files, so without this test a backend
field rename would keep backend unit tests, ``tsc`` and mocked e2e all green while the
UI rendered ``undefined``/``--`` against the live API. This test closes that drift.

Marked ``e2e`` (needs the real DB + FastAPI app). Default addopts exclude it; run
container-side (host has no DB route), e.g.::

    uv run pytest tests/test_market_contract.py -v -m e2e

Covered endpoints (Task 8 added the four enveloped list endpoints; they previously
had no backend contract lock, so only the frontend e2e mocks — which the backend
tests never load — fixed their shape):

- ``/market/rankings``, ``/market/sw-industry/performance`` — object envelopes;
- ``/market/distribution``, ``/market/sectors``, ``/market/capital-flow``,
  ``/market/hot-boards`` — ``MarketListOut`` envelopes
  (``{as_of, as_of_quality, as_of_reason, items}``).

Fixture resolution is **lazy** and skips when the frontend tree is absent: pytest
imports a module to read its ``pytestmark`` before ``-m`` deselection applies, so a
module-level path lookup that raised would turn a backend-only ``uv run pytest`` into a
collection error. The skip is **only** for an absent fixtures *directory*: once the
directory exists, a named file that is missing (renamed/deleted fixture) is a hard
failure — a silent skip would make the contract lock evaporate exactly when it is
needed.
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
        # Directory exists => this is a real checkout; a missing named fixture means it
        # was renamed/deleted. Failing (not skipping) keeps the contract lock honest.
        pytest.fail(f"fixture {name} is missing from {fixtures_dir}")
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
            assert set(body["items"][0]) == set(sample["items"][0]), (
                f"item keys drifted for {rank_type}"
            )


#: Task 8: the four enveloped list endpoints. ``params``/``fixture`` stay alongside the
#: path so a fixture rename cannot silently turn the test into a skip.
_ENVELOPE_ENDPOINTS: list[tuple[str, dict[str, str], str]] = [
    ("/api/v1/market/distribution", {}, "distribution.sample.json"),
    ("/api/v1/market/sectors", {}, "sectors.sample.json"),
    ("/api/v1/market/capital-flow", {}, "capitalFlow.sample.json"),
    ("/api/v1/market/hot-boards", {"category": "industry"}, "hotBoards.sample.json"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("path", "params", "fixture_name"),
    _ENVELOPE_ENDPOINTS,
    ids=[path.rsplit("/", 1)[-1] for path, _p, _f in _ENVELOPE_ENDPOINTS],
)
async def test_enveloped_list_endpoints_emit_fixture_keyset(
    path: str, params: dict[str, str], fixture_name: str
) -> None:
    """Every ``MarketListOut`` endpoint must emit exactly the mocked key set.

    The fixture file is the shared source of truth with the homepage e2e mock, so a
    field rename on either side reds this test (previously only rankings/SW had it).
    """
    fixture = _load(fixture_name)
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(path, params=params)
    assert resp.status_code == 200, (path, resp.status_code)
    body = resp.json()
    assert set(body) == set(fixture), f"response envelope drifted for {path}"
    assert body["items"], f"fixture produced no rows for {path}"
    assert set(body["items"][0]) == set(fixture["items"][0]), f"item keys drifted for {path}"
    # every item must carry the same key set (not just the first)
    assert all(set(item) == set(fixture["items"][0]) for item in body["items"])


@pytest.mark.asyncio
async def test_distribution_keeps_the_fixed_bucket_order() -> None:
    """The 11 buckets are always all present, in the legacy display order.

    Data-independent (a resolved day zero-fills every bucket), so the fixture's first
    three ranges are a stable order contract.
    """
    fixture = _load("distribution.sample.json")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/distribution")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 11, "a resolved day must emit all 11 zero-filled buckets"
    assert [item["range"] for item in items[:3]] == [item["range"] for item in fixture["items"]]


@pytest.mark.asyncio
async def test_sw_performance_emits_fixture_keyset() -> None:
    fixture = _load("swPerformance.sample.json")
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/sw-industry/performance", params={"limit": 3})
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == set(fixture)
    assert body["items"], "fixture produced no SW L1 rows"
    assert set(body["items"][0]) == set(fixture["items"][0])
