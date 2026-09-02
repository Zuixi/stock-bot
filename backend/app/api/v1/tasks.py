"""Task endpoints: list, trigger, get status, cancel."""

import uuid

from fastapi import APIRouter, Query, status

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import conflict_response, not_found_response
from app.schemas.common import PagedResponse, PageParams
from app.schemas.task import (
    FetchDailyBasicRequest,
    FetchIndustryMetricsRequest,
    FetchIndustrySecuritiesRequest,
    FetchQuotesRequest,
    FetchUniverseRequest,
    RunClusteringRequest,
    TaskOut,
)
from app.services import task_service

router = APIRouter()


@router.get("", response_model=PagedResponse[TaskOut])
async def list_tasks(
    db: DbDep,
    cache: CacheDep,
    type: str | None = Query(None, description="Filter by task type"),
    status_filter: str | None = Query(None, alias="status", description="Filter by status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> PagedResponse[TaskOut]:
    """List background tasks with optional filters."""
    page_params = PageParams(page=page, page_size=page_size)
    items, total = await task_service.list_tasks(
        db, cache, type=type, status=status_filter, page_params=page_params
    )
    return PagedResponse.build(items=items, total=total, params=page_params)


@router.post(
    "/fetch-universe",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_universe(req: FetchUniverseRequest, db: DbDep) -> TaskOut:
    """Trigger a universe fetch task for a specific exchange."""
    return await task_service.trigger_fetch_universe(db, req)


@router.post(
    "/fetch-quotes",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_quotes(req: FetchQuotesRequest, db: DbDep) -> TaskOut:
    """Trigger a quotes fetch task."""
    return await task_service.trigger_fetch_quotes(db, req)


@router.post(
    "/fetch-daily-basic",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_daily_basic(req: FetchDailyBasicRequest, db: DbDep) -> TaskOut:
    """Trigger a daily_basic fetch task (entire market per trade_date)."""
    return await task_service.trigger_fetch_daily_basic(db, req)


@router.post(
    "/run-clustering",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def run_clustering(req: RunClusteringRequest, db: DbDep) -> TaskOut:
    """Trigger a clustering run."""
    return await task_service.trigger_clustering(db, req)


@router.post(
    "/fetch-industry-metrics",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_industry_metrics(req: FetchIndustryMetricsRequest, db: DbDep) -> TaskOut:
    """Trigger an industry metrics ingest task (mock/AKShare)."""
    return await task_service.trigger_fetch_industry_metrics(db, req)


@router.post(
    "/fetch-securities",
    response_model=TaskOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def fetch_securities(req: FetchIndustrySecuritiesRequest, db: DbDep) -> TaskOut:
    """Trigger an ETF/convertible-bond daily fetch task (TuShare fund/cb daily)."""
    return await task_service.trigger_fetch_securities(db, req)


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: uuid.UUID,
    db: DbDep,
    cache: CacheDep,
) -> TaskOut:
    """Get task status by ID."""
    task = await task_service.get_task(db, cache, task_id)
    if task is None:
        raise not_found_response("Task", str(task_id))
    return task


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_task(task_id: uuid.UUID, db: DbDep, cache: CacheDep) -> None:
    """Cancel a pending or running task."""
    ok = await task_service.cancel_task(db, cache, task_id)
    if not ok:
        raise conflict_response("Task already completed, failed, or cancelled — cannot cancel")
