"""Task endpoints: trigger background jobs and check status."""

import uuid

from fastapi import APIRouter, status

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.task import (
    FetchQuotesRequest,
    FetchUniverseRequest,
    RunClusteringRequest,
    TaskOut,
)
from app.services import task_service

router = APIRouter()


@router.post(
    "/fetch-universe",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_universe(req: FetchUniverseRequest, db: DbDep) -> TaskOut:
    return await task_service.trigger_fetch_universe(db, req)


@router.post(
    "/fetch-quotes",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_quotes(req: FetchQuotesRequest, db: DbDep) -> TaskOut:
    return await task_service.trigger_fetch_quotes(db, req)


@router.post(
    "/run-clustering",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_clustering(req: RunClusteringRequest, db: DbDep) -> TaskOut:
    return await task_service.trigger_clustering(db, req)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(task_id: uuid.UUID, db: DbDep, cache: CacheDep) -> TaskOut:
    task = await task_service.get_task(db, cache, task_id)
    if task is None:
        raise not_found_response("Task", str(task_id))
    return task
