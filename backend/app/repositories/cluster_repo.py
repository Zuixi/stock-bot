"""Cluster repository: runs, members, explanations."""

import uuid

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.cluster import ClusterExplanation, ClusteringMember, ClusteringRun
from app.models.stock import Stock


async def list_runs(db: AsyncSession, limit: int = 20) -> list[ClusteringRun]:
    stmt = select(ClusteringRun).order_by(desc(ClusteringRun.created_at)).limit(limit)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> ClusteringRun | None:
    return await db.get(ClusteringRun, run_id)


async def get_default_run(db: AsyncSession) -> ClusteringRun | None:
    stmt = select(ClusteringRun).where(ClusteringRun.is_default.is_(True)).limit(1)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_cluster_distribution(
    db: AsyncSession, run_id: uuid.UUID
) -> list[tuple[int, int]]:
    """Returns [(cluster_label, count), ...] ordered by cluster_label."""
    stmt = (
        select(ClusteringMember.cluster_label, func.count().label("count"))
        .where(ClusteringMember.run_id == run_id)
        .group_by(ClusteringMember.cluster_label)
        .order_by(ClusteringMember.cluster_label)
    )
    result = await db.execute(stmt)
    return [(row.cluster_label, row.count) for row in result]


async def get_cluster_members(
    db: AsyncSession, run_id: uuid.UUID, cluster_label: int, offset: int, limit: int
) -> tuple[list[dict], int]:
    count_stmt = (
        select(func.count())
        .select_from(ClusteringMember)
        .where(
            ClusteringMember.run_id == run_id,
            ClusteringMember.cluster_label == cluster_label,
        )
    )
    total = (await db.execute(count_stmt)).scalar_one()

    stmt = (
        select(
            ClusteringMember.stock_id,
            ClusteringMember.cluster_label,
            ClusteringMember.distance,
            Stock.symbol,
            Stock.name,
            Stock.exchange,
        )
        .join(Stock, ClusteringMember.stock_id == Stock.id)
        .where(
            ClusteringMember.run_id == run_id,
            ClusteringMember.cluster_label == cluster_label,
        )
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = [
        {
            "stock_id": row.stock_id,
            "symbol": row.symbol,
            "name": row.name,
            "exchange": row.exchange,
            "cluster_label": row.cluster_label,
            "distance": float(row.distance) if row.distance else None,
        }
        for row in result
    ]
    return rows, total


async def list_explanations(
    db: AsyncSession, run_id: uuid.UUID
) -> list[ClusterExplanation]:
    stmt = (
        select(ClusterExplanation)
        .where(ClusterExplanation.run_id == run_id)
        .order_by(ClusterExplanation.cluster_label)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def create_run(db: AsyncSession, run: ClusteringRun) -> ClusteringRun:
    db.add(run)
    await db.flush()
    return run
