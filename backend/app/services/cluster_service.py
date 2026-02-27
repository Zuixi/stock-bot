"""Cluster service: runs, distribution, members, explanations."""

import uuid
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.redis import CacheClient
from app.repositories import cluster_repo
from app.schemas.cluster import (
    ClusterDistributionItem,
    ClusterDistributionOut,
    ClusterExplanationOut,
    ClusterMemberOut,
    ClusteringRunOut,
)
from app.schemas.common import PageParams

logger = logging.getLogger(__name__)


async def list_runs(
    db: AsyncSession, cache: CacheClient, limit: int = 20
) -> list[ClusteringRunOut]:
    cached = await cache.get(f"cluster:runs:{limit}")
    if cached:
        return [ClusteringRunOut(**r) for r in cached]

    runs = await cluster_repo.list_runs(db, limit)
    result = [ClusteringRunOut.model_validate(r) for r in runs]
    await cache.set(f"cluster:runs:{limit}", [r.model_dump(mode="json") for r in result], ttl=1800)
    return result


async def get_run(
    db: AsyncSession, cache: CacheClient, run_id: uuid.UUID
) -> ClusteringRunOut | None:
    cache_key = f"cluster:run:{run_id}"
    cached = await cache.get(cache_key)
    if cached:
        return ClusteringRunOut(**cached)

    run = await cluster_repo.get_run(db, run_id)
    if run is None:
        return None
    out = ClusteringRunOut.model_validate(run)
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=3600)
    return out


async def get_distribution(
    db: AsyncSession, cache: CacheClient, run_id: uuid.UUID
) -> ClusterDistributionOut | None:
    run = await cluster_repo.get_run(db, run_id)
    if run is None:
        return None

    cache_key = f"cluster:dist:{run_id}"
    cached = await cache.get(cache_key)
    if cached:
        return ClusterDistributionOut(**cached)

    rows = await cluster_repo.get_cluster_distribution(db, run_id)
    total = sum(count for _, count in rows)
    dist = [
        ClusterDistributionItem(
            cluster_label=label,
            count=count,
            percentage=round(count / total * 100, 2) if total else 0.0,
        )
        for label, count in rows
    ]
    out = ClusterDistributionOut(run_id=run_id, total=total, distribution=dist)
    await cache.set(cache_key, out.model_dump(mode="json"), ttl=1800)
    return out


async def get_members(
    db: AsyncSession,
    run_id: uuid.UUID,
    cluster_label: int,
    page_params: PageParams,
) -> tuple[list[ClusterMemberOut], int]:
    rows, total = await cluster_repo.get_cluster_members(
        db, run_id, cluster_label, page_params.offset, page_params.page_size
    )
    return [ClusterMemberOut(**r) for r in rows], total


async def list_explanations(
    db: AsyncSession, cache: CacheClient, run_id: uuid.UUID
) -> list[ClusterExplanationOut]:
    cache_key = f"cluster:explanations:{run_id}"
    cached = await cache.get(cache_key)
    if cached:
        return [ClusterExplanationOut(**e) for e in cached]

    explanations = await cluster_repo.list_explanations(db, run_id)
    result = [ClusterExplanationOut.model_validate(e) for e in explanations]
    await cache.set(cache_key, [e.model_dump(mode="json") for e in result], ttl=1800)
    return result
