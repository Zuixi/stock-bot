"""Task service: create, list, cancel tasks and dispatch messages to RabbitMQ."""

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.mq import publish_message
from app.core.redis import CacheClient
from app.models.task import Task
from app.repositories import task_repo
from app.schemas.common import PageParams
from app.schemas.task import (
    FetchDailyBasicRequest,
    FetchIndustryMetricsRequest,
    FetchIndustrySecuritiesRequest,
    FetchQuotesRequest,
    FetchUniverseRequest,
    MarketDataFetchRequest,
    RunClusteringRequest,
    TaskOut,
)

logger = logging.getLogger(__name__)


async def _dispatch_task(
    db: AsyncSession, task_type: str, queue_key: str, payload: dict
) -> Task:
    """创建任务行 → 先提交、后发消息。

    提交必须先于 publish：worker 消费到消息时任务行必须已可见，
    否则 update_task_status 查无此行会静默跳过，任务永远停在 pending。
    publish 失败时把任务标记为 failed，避免留下无消息的孤儿 pending。
    """
    task = await task_repo.create_task(db, task_type, payload)
    await db.commit()
    try:
        await publish_message(
            queue_key,
            {"task_id": str(task.id), "type": task_type, "payload": payload},
        )
    except Exception as exc:
        await task_repo.update_task_status(
            db, task.id, "failed", error=f"publish failed: {exc}"
        )
        await db.commit()
        raise
    return task


async def trigger_fetch_universe(
    db: AsyncSession, req: FetchUniverseRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(db, "fetch_universe", "universe.fetch", payload)
    logger.info("Dispatched fetch_universe task %s for exchange=%s", task.id, req.exchange)
    return TaskOut.model_validate(task)


async def trigger_fetch_quotes(
    db: AsyncSession, req: FetchQuotesRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(db, "fetch_quotes", "quotes.fetch", payload)
    logger.info("Dispatched fetch_quotes task %s", task.id)
    return TaskOut.model_validate(task)


async def trigger_fetch_daily_basic(
    db: AsyncSession, req: FetchDailyBasicRequest
) -> TaskOut:
    """Trigger a daily_basic fetch task (entire market per trade_date)."""
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(db, "fetch_daily_basic", "daily_basic.fetch", payload)
    logger.info("Dispatched fetch_daily_basic task %s", task.id)
    return TaskOut.model_validate(task)


async def trigger_clustering(
    db: AsyncSession, req: RunClusteringRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(db, "run_clustering", "clustering.run", payload)
    logger.info("Dispatched clustering task %s, algorithm=%s", task.id, req.algorithm)
    return TaskOut.model_validate(task)


async def trigger_fetch_industry_metrics(
    db: AsyncSession, req: FetchIndustryMetricsRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(
        db, "fetch_industry_metrics", "industry_metrics.fetch", payload
    )
    logger.info(
        "Dispatched fetch_industry_metrics task %s, industry=%s", task.id, req.industry_key
    )
    return TaskOut.model_validate(task)


async def trigger_fetch_securities(
    db: AsyncSession, req: FetchIndustrySecuritiesRequest
) -> TaskOut:
    payload = req.model_dump(exclude_none=True)
    task = await _dispatch_task(db, "fetch_securities", "securities.fetch", payload)
    logger.info(
        "Dispatched fetch_securities task %s, industry=%s backfill_days=%s",
        task.id, req.industry_key, req.backfill_days,
    )
    return TaskOut.model_validate(task)


async def trigger_fetch_market_data(
    db: AsyncSession, req: MarketDataFetchRequest
) -> TaskOut:
    """Trigger a market-data ingest task (worker dispatches by req.type)."""
    payload = {"type": req.type, **(req.params or {})}
    task = await _dispatch_task(db, "fetch_market_data", "market_data.fetch", payload)
    logger.info("Dispatched fetch_market_data task %s, type=%s", task.id, req.type)
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


async def list_tasks(
    db: AsyncSession,
    cache: CacheClient,
    type: str | None = None,
    status: str | None = None,
    page_params: PageParams | None = None,
) -> tuple[list[TaskOut], int]:
    if page_params is None:
        page_params = PageParams()
    tasks = await task_repo.list_tasks(
        db,
        task_type=type,
        status=status,
        offset=page_params.offset,
        limit=page_params.page_size,
    )
    total = await task_repo.count_tasks(db, task_type=type, status=status)
    return [TaskOut.model_validate(t) for t in tasks], total


async def cancel_task(
    db: AsyncSession, cache: CacheClient, task_id: uuid.UUID
) -> bool:
    """Cancel a task if it is still pending or running. Returns True on success."""
    task = await task_repo.get_task(db, task_id)
    if task is None:
        return False
    if task.status not in ("pending", "running"):
        return False
    await task_repo.update_task_status(db, task_id, "cancelled")
    await cache.delete(f"task:status:{task_id}")
    logger.info("Cancelled task %s", task_id)
    return True
