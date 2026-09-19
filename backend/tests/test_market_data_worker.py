"""MarketDataWorker 分发单测。"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest

from app.workers.market_data_worker import MarketDataWorker


class _NullSession:
    async def commit(self) -> None:
        return None


@asynccontextmanager
async def _fake_session_factory() -> AsyncIterator[Any]:
    yield _NullSession()


async def test_worker_dispatches_northbound(monkeypatch):
    called: list = []

    async def fake_ingest(db, days=30):
        called.append(days)
        return {"upserted": 3}

    from app.services import market_data_service as mds

    monkeypatch.setattr(mds, "ingest_northbound", fake_ingest)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    worker = MarketDataWorker()
    result = await worker.process(task_id=None, payload={"type": "northbound", "days": 7})
    assert result["status"] == "completed" and result["type"] == "northbound"
    assert called == [7]


async def test_worker_propagates_service_failure(monkeypatch):
    async def fake_ingest(db, days=30):
        raise RuntimeError("source down")

    from app.services import market_data_service as mds

    monkeypatch.setattr(mds, "ingest_northbound", fake_ingest)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    worker = MarketDataWorker()
    with pytest.raises(RuntimeError):
        await worker.process(task_id=None, payload={"type": "northbound", "days": 7})


async def test_worker_unknown_type_fails_cleanly():
    worker = MarketDataWorker()
    result = await worker.process(task_id=None, payload={"type": "nope"})
    assert result["status"] == "failed"


async def test_worker_dispatches_concept_members(monkeypatch):
    called: list = []

    async def fake_ingest(db):
        called.append(True)
        return {"boards": 1, "members_upserted": 2}

    from app.services import concept_service

    monkeypatch.setattr(concept_service, "ingest_concept_members", fake_ingest)
    monkeypatch.setattr(
        "app.workers.market_data_worker.async_session_factory", _fake_session_factory
    )
    worker = MarketDataWorker()
    result = await worker.process(task_id=None, payload={"type": "concept_members"})
    assert result["status"] == "completed" and result["type"] == "concept_members"
    assert result["boards"] == 1
    assert called == [True]
