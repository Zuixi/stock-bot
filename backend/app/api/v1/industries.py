"""Industry research workbench endpoints.

    /api/v1/industries
        GET   /                                已产品化行业列表（含指标覆盖度）
        GET   /{key}/dashboard                 看板聚合（指标带/速览/趋势/周期/信号/仓位）
        GET   /{key}/metrics/latest            全部指标最新值（源优先级裁决 + 预警标签）
        GET   /{key}/metrics/{metric_key}/history
        POST  /{key}/metrics/batch             人工/CSV 导入通道（幂等 upsert）
"""

from fastapi import APIRouter, Query, status

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.industry import (
    DashboardOut,
    IndustrySummaryOut,
    MetricBatchRequest,
    MetricBatchResponse,
    MetricHistoryOut,
    MetricLatestOut,
)
from app.services import industry_metric_service as service

router = APIRouter()


@router.get("", response_model=list[IndustrySummaryOut])
async def list_industries(db: DbDep, cache: CacheDep) -> list[IndustrySummaryOut]:
    """List productized industries with metric coverage."""
    return await service.list_industries(db, cache)


@router.get("/{industry_key}/dashboard", response_model=DashboardOut)
async def get_dashboard(industry_key: str, db: DbDep, cache: CacheDep) -> DashboardOut:
    """One-shot dashboard aggregate for the workbench."""
    try:
        return await service.get_dashboard(db, cache, industry_key)
    except service.UnknownIndustryError as exc:
        raise not_found_response("Industry", industry_key) from exc


@router.get("/{industry_key}/metrics/latest", response_model=list[MetricLatestOut])
async def get_latest_metrics(
    industry_key: str,
    db: DbDep,
    cache: CacheDep,
    group: str | None = Query(None, description="Filter by display group (strip/quick/supply/cost)"),
) -> list[MetricLatestOut]:
    try:
        return await service.get_latest_metrics(db, cache, industry_key, group=group)
    except service.UnknownIndustryError as exc:
        raise not_found_response("Industry", industry_key) from exc


@router.get("/{industry_key}/metrics/{metric_key}/history", response_model=MetricHistoryOut)
async def get_metric_history(
    industry_key: str,
    metric_key: str,
    db: DbDep,
    cache: CacheDep,
    months: int = Query(default=36, ge=1, le=240),
    freq: str | None = Query(None, description="Override metric default freq"),
    source: str | None = Query(None, description="Filter by data source"),
) -> MetricHistoryOut:
    try:
        return await service.get_metric_history(
            db, cache, industry_key, metric_key, months=months, freq=freq, source=source
        )
    except service.UnknownIndustryError as exc:
        raise not_found_response("Industry", industry_key) from exc
    except service.UnknownMetricError as exc:
        raise not_found_response("Metric", metric_key) from exc


@router.post(
    "/{industry_key}/metrics/batch",
    response_model=MetricBatchResponse,
    status_code=status.HTTP_200_OK,
)
async def batch_upsert_metrics(
    industry_key: str, req: MetricBatchRequest, db: DbDep
) -> MetricBatchResponse:
    """Manual/CSV import channel (idempotent)."""
    try:
        result = await service.batch_upsert_metrics(
            db, industry_key,
            [item.model_dump() for item in req.items],
            recompute_derived=req.recompute_derived,
        )
    except service.UnknownIndustryError as exc:
        raise not_found_response("Industry", industry_key) from exc
    return MetricBatchResponse(**result)
