"""Cluster endpoints: runs, distribution, members, explanations."""

import uuid

from fastapi import APIRouter, Query

from app.api.deps import CacheDep, DbDep
from app.core.exceptions import not_found_response
from app.schemas.cluster import (
    ClusterDistributionOut,
    ClusterExplanationOut,
    ClusterMemberOut,
    ClusteringRunOut,
)
from app.schemas.common import PageParams, PagedResponse
from app.services import cluster_service

router = APIRouter()


@router.get("/runs", response_model=list[ClusteringRunOut])
async def list_runs(
    db: DbDep,
    cache: CacheDep,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[ClusteringRunOut]:
    return await cluster_service.list_runs(db, cache, limit)


@router.get("/{run_id}", response_model=ClusteringRunOut)
async def get_run(
    run_id: uuid.UUID,
    db: DbDep,
    cache: CacheDep,
) -> ClusteringRunOut:
    result = await cluster_service.get_run(db, cache, run_id)
    if result is None:
        raise not_found_response("ClusteringRun", str(run_id))
    return result


@router.get("/{run_id}/distribution", response_model=ClusterDistributionOut)
async def get_distribution(
    run_id: uuid.UUID,
    db: DbDep,
    cache: CacheDep,
) -> ClusterDistributionOut:
    result = await cluster_service.get_distribution(db, cache, run_id)
    if result is None:
        raise not_found_response("ClusteringRun", str(run_id))
    return result


@router.get("/{run_id}/members/{label}", response_model=PagedResponse[ClusterMemberOut])
async def get_members(
    run_id: uuid.UUID,
    label: int,
    db: DbDep,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> PagedResponse[ClusterMemberOut]:
    page_params = PageParams(page=page, page_size=page_size)
    items, total = await cluster_service.get_members(db, run_id, label, page_params)
    return PagedResponse.build(items=items, total=total, params=page_params)


@router.get("/{run_id}/explanations", response_model=list[ClusterExplanationOut])
async def list_explanations(
    run_id: uuid.UUID,
    db: DbDep,
    cache: CacheDep,
) -> list[ClusterExplanationOut]:
    return await cluster_service.list_explanations(db, cache, run_id)
