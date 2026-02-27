"""Task service: create tasks and dispatch messages to RabbitMQ."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mq import publish_message
from app.core.redis import CacheClient
from app.repositories import task_repo
from app.schemas.task import (
    FetchQuotesRequest,
    FetchUniverseRequest,
    RunClusteringRequest,
    TaskOut,
)

logger = logging.getLogger(__name__)


async def trigger_fetch_universe(
    db: AsyncSession, req: FetchUniverseRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await task_repo.create_task(db, "fetch_universe", payload)
    await publish_message(
        "universe.fetch",
        {"task_id": str(task.id), "type": "fetch_universe", "payload": payload},
    )
    logger.info("Dispatched fetch_universe task %s for exchange=%s", task.id, req.exchange)
    return TaskOut.model_validate(task)


async def trigger_fetch_quotes(
    db: AsyncSession, req: FetchQuotesRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await task_repo.create_task(db, "fetch_quotes", payload)
    await publish_message(
        "quotes.fetch",
        {"task_id": str(task.id), "type": "fetch_quotes", "payload": payload},
    )
    logger.info("Dispatched fetch_quotes task %s", task.id)
    return TaskOut.model_validate(task)


async def trigger_clustering(
    db: AsyncSession, req: RunClusteringRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await task_repo.create_task(db, "run_clustering", payload)
    await publish_message(
        "clustering.run",
        {"task_id": str(task.id), "type": "run_clustering", "payload": payload},
    )
    logger.info("Dispatched clustering task %s, algorithm=%s", task.id, req.algorithm)
    return TaskOut.model_validate(task)


async def get_task(
    db: AsyncSession, cache: CacheClient, task_id: uuid.UUID
) -> TaskOut | None:
    cache_key = f"task:status:{task_id}"
    cached = await cache.get(cache_key)
    if cached:
        task_out = TaskOut(**cached)
        if task_out.status in ("completed", "failed", "cancelled"):
            return task_out

    task = await task_repo.get_task(db, task_id)
    if task is None:
        return None
    out = TaskOut.model_validate(task)
    ttl = 3600 if out.status in ("completed", "failed", "cancelled") else 10
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=ttl)
    return out
