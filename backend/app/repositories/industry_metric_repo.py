"""Industry research repository: metric upsert/queries, reference points, signals."""

from __future__ import annotations

from datetime import date

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import (
    IndustryMetric,
    IndustryReferencePoint,
    IndustrySignal,
)

_METRIC_CONFLICT_COLS = ("industry_key", "stock_id", "metric_key", "source", "freq", "period")


async def upsert_metrics(db: AsyncSession, rows: list[dict]) -> int:
    """Idempotent bulk upsert of metric rows. Returns affected row count."""
    if not rows:
        return 0
    stmt = pg_insert(IndustryMetric).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_industry_metrics_key",
        set_={
            "source_tier": stmt.excluded.source_tier,
            "freq": stmt.excluded.freq,
            "value": stmt.excluded.value,
            "unit": stmt.excluded.unit,
            "extra": stmt.excluded.extra,
        },
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def latest_rows_by_metric(db: AsyncSession, industry_key: str) -> dict[str, list[IndustryMetric]]:
    """Latest row per (metric_key, source, freq) —DISTINCT ON over a small keyed table."""
    stmt = (
        select(IndustryMetric)
        .where(IndustryMetric.industry_key == industry_key, IndustryMetric.stock_id == 0)
        .distinct(IndustryMetric.metric_key, IndustryMetric.source, IndustryMetric.freq)
        .order_by(
            IndustryMetric.metric_key, IndustryMetric.source,
            IndustryMetric.freq, desc(IndustryMetric.period),
        )
    )
    result = await db.execute(stmt)
    grouped: dict[str, list[IndustryMetric]] = {}
    for row in result.scalars():
        grouped.setdefault(row.metric_key, []).append(row)
    return grouped


async def get_metric_history(
    db: AsyncSession,
    industry_key: str,
    metric_key: str,
    limit: int = 240,
    freq: str | None = None,
    source: str | None = None,
) -> list[IndustryMetric]:
    """Ascending series for one metric (optionally filtered by freq/source)."""
    stmt = (
        select(IndustryMetric)
        .where(
            IndustryMetric.industry_key == industry_key,
            IndustryMetric.stock_id == 0,
            IndustryMetric.metric_key == metric_key,
        )
        .order_by(desc(IndustryMetric.period))
        .limit(limit)
    )
    if freq:
        stmt = stmt.where(IndustryMetric.freq == freq)
    if source:
        stmt = stmt.where(IndustryMetric.source == source)
    result = await db.execute(stmt)
    return list(reversed(result.scalars().all()))


# ── Reference points ──────────────────────────────────────────────────

async def list_reference_points(
    db: AsyncSession, industry_key: str, metric_key: str
) -> list[IndustryReferencePoint]:
    stmt = (
        select(IndustryReferencePoint)
        .where(
            IndustryReferencePoint.industry_key == industry_key,
            IndustryReferencePoint.metric_key == metric_key,
        )
        .order_by(IndustryReferencePoint.effective_from)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert_reference_points(db: AsyncSession, rows: list[dict]) -> int:
    if not rows:
        return 0
    stmt = pg_insert(IndustryReferencePoint).values(rows)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_industry_reference_points",
        set_={"value": stmt.excluded.value, "note": stmt.excluded.note},
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


def applicable_reference(
    points: list[IndustryReferencePoint], as_of: date | None = None
) -> IndustryReferencePoint | None:
    """Latest anchor whose effective_from <= as_of (policy revisions applied by date)."""
    as_of = as_of or date.today()
    applicable = [p for p in points if p.effective_from <= as_of]
    return applicable[-1] if applicable else None


# ── Signals ───────────────────────────────────────────────────────────

async def upsert_signal(db: AsyncSession, row: dict) -> IndustrySignal:
    stmt = pg_insert(IndustrySignal).values(row)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_industry_signals_date",
        set_={
            "phase": stmt.excluded.phase,
            "signal_type": stmt.excluded.signal_type,
            "positions": stmt.excluded.positions,
            "reason": stmt.excluded.reason,
            "basis": stmt.excluded.basis,
        },
    ).returning(IndustrySignal)
    result = await db.execute(stmt)
    return result.scalar_one()


async def latest_signal(db: AsyncSession, industry_key: str) -> IndustrySignal | None:
    stmt = (
        select(IndustrySignal)
        .where(IndustrySignal.industry_key == industry_key)
        .order_by(desc(IndustrySignal.effective_date))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_signals(
    db: AsyncSession, industry_key: str, limit: int = 20
) -> list[IndustrySignal]:
    stmt = (
        select(IndustrySignal)
        .where(IndustrySignal.industry_key == industry_key)
        .order_by(desc(IndustrySignal.effective_date))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_mock_rows(db: AsyncSession, industry_key: str) -> int:
    """Purge demo/mock rows once a real source has landed (mock never masquerades as data)."""
    stmt = delete(IndustryMetric).where(
        IndustryMetric.industry_key == industry_key,
        IndustryMetric.stock_id == 0,
        IndustryMetric.source == "mock",
    )
    result = await db.execute(stmt)
    return result.rowcount or 0
