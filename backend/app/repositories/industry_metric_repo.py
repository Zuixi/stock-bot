"""Industry research repository: metric upsert/queries, reference points, signals."""

from __future__ import annotations

from datetime import date
from typing import cast

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import (
    IndustryDataQualitySnapshot,
    IndustryMetric,
    IndustryReferencePoint,
    IndustrySignal,
    IndustrySignalEvaluation,
    IndustrySignalEvent,
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
            "value": stmt.excluded.value,
            "unit": stmt.excluded.unit,
            "extra": stmt.excluded.extra,
        },
    )
    result = await db.execute(stmt)
    return result.rowcount or 0


async def latest_rows_by_metric(
    db: AsyncSession, industry_key: str
) -> dict[str, list[IndustryMetric]]:
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


# ── 公司级指标（标的分析，P5）：stock_id > 0 ─────────────────────────

async def get_company_metric_history(
    db: AsyncSession,
    industry_key: str,
    metric_key: str,
    limit: int = 4000,
    freq: str | None = None,
    source: str | None = None,
) -> list[IndustryMetric]:
    """Ascending series of one company metric across ALL stocks (stock_id > 0)."""
    stmt = (
        select(IndustryMetric)
        .where(
            IndustryMetric.industry_key == industry_key,
            IndustryMetric.stock_id > 0,
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


async def latest_company_rows(
    db: AsyncSession, industry_key: str
) -> dict[tuple[int, str], list[IndustryMetric]]:
    """Latest row per (stock_id, metric_key, source, freq), grouped by (stock_id, metric_key)."""
    stmt = (
        select(IndustryMetric)
        .where(
            IndustryMetric.industry_key == industry_key,
            IndustryMetric.stock_id > 0,
        )
        .distinct(
            IndustryMetric.stock_id, IndustryMetric.metric_key,
            IndustryMetric.source, IndustryMetric.freq,
        )
        .order_by(
            IndustryMetric.stock_id, IndustryMetric.metric_key,
            IndustryMetric.source, IndustryMetric.freq, desc(IndustryMetric.period),
        )
    )
    result = await db.execute(stmt)
    grouped: dict[tuple[int, str], list[IndustryMetric]] = {}
    for row in result.scalars():
        grouped.setdefault((row.stock_id, row.metric_key), []).append(row)
    return grouped


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


# ── Data quality snapshots and immutable signal verification ───────────

async def upsert_quality_snapshot(
    db: AsyncSession, row: dict
) -> IndustryDataQualitySnapshot:
    insert_stmt = pg_insert(IndustryDataQualitySnapshot).values(row)
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_industry_quality_date",
        set_={
            "status": insert_stmt.excluded.status,
            "signal_ready": insert_stmt.excluded.signal_ready,
            "ready_count": insert_stmt.excluded.ready_count,
            "missing_count": insert_stmt.excluded.missing_count,
            "stale_count": insert_stmt.excluded.stale_count,
            "rejected_count": insert_stmt.excluded.rejected_count,
            "partial_count": insert_stmt.excluded.partial_count,
            "details": insert_stmt.excluded.details,
            "updated_at": insert_stmt.excluded.updated_at,
        },
    ).returning(IndustryDataQualitySnapshot)
    result = await db.execute(stmt)
    return cast(IndustryDataQualitySnapshot, result.scalar_one())


async def latest_quality_snapshot(
    db: AsyncSession, industry_key: str
) -> IndustryDataQualitySnapshot | None:
    stmt = (
        select(IndustryDataQualitySnapshot)
        .where(IndustryDataQualitySnapshot.industry_key == industry_key)
        .order_by(desc(IndustryDataQualitySnapshot.as_of))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def latest_signal_event(
    db: AsyncSession, industry_key: str
) -> IndustrySignalEvent | None:
    stmt = (
        select(IndustrySignalEvent)
        .where(IndustrySignalEvent.industry_key == industry_key)
        .order_by(desc(IndustrySignalEvent.event_date), desc(IndustrySignalEvent.id))
        .limit(1)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def create_signal_event(
    db: AsyncSession, row: dict
) -> IndustrySignalEvent | None:
    stmt = (
        pg_insert(IndustrySignalEvent)
        .values(row)
        .on_conflict_do_nothing(constraint="uq_industry_signal_event")
        .returning(IndustrySignalEvent)
    )
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def list_signal_events(
    db: AsyncSession, industry_key: str, limit: int = 20
) -> list[IndustrySignalEvent]:
    stmt = (
        select(IndustrySignalEvent)
        .where(IndustrySignalEvent.industry_key == industry_key)
        .order_by(desc(IndustrySignalEvent.event_date), desc(IndustrySignalEvent.id))
        .limit(limit)
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def upsert_signal_evaluation(
    db: AsyncSession, row: dict
) -> IndustrySignalEvaluation:
    insert_stmt = pg_insert(IndustrySignalEvaluation).values(row)
    stmt = insert_stmt.on_conflict_do_update(
        constraint="uq_industry_signal_evaluation",
        set_={
            "target_date": insert_stmt.excluded.target_date,
            "status": insert_stmt.excluded.status,
            "rules": insert_stmt.excluded.rules,
            "start_snapshot": insert_stmt.excluded.start_snapshot,
            "end_snapshot": insert_stmt.excluded.end_snapshot,
            "criteria_results": insert_stmt.excluded.criteria_results,
            "insufficient_reasons": insert_stmt.excluded.insufficient_reasons,
            "score": insert_stmt.excluded.score,
            "evaluated_at": insert_stmt.excluded.evaluated_at,
            "updated_at": insert_stmt.excluded.updated_at,
        },
    ).returning(IndustrySignalEvaluation)
    result = await db.execute(stmt)
    return cast(IndustrySignalEvaluation, result.scalar_one())


async def list_due_signal_evaluations(
    db: AsyncSession, industry_key: str, as_of: date
) -> list[IndustrySignalEvaluation]:
    stmt = (
        select(IndustrySignalEvaluation)
        .join(
            IndustrySignalEvent,
            IndustrySignalEvent.id == IndustrySignalEvaluation.signal_event_id,
        )
        .where(
            IndustrySignalEvent.industry_key == industry_key,
            IndustrySignalEvaluation.status == "pending",
            IndustrySignalEvaluation.target_date <= as_of,
        )
        .order_by(
            IndustrySignalEvaluation.target_date.asc(),
            IndustrySignalEvaluation.signal_event_id.asc(),
            IndustrySignalEvaluation.horizon_days.asc(),
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def list_event_evaluations(
    db: AsyncSession, event_ids: list[int]
) -> list[IndustrySignalEvaluation]:
    if not event_ids:
        return []
    stmt = (
        select(IndustrySignalEvaluation)
        .where(IndustrySignalEvaluation.signal_event_id.in_(event_ids))
        .order_by(
            IndustrySignalEvaluation.signal_event_id.asc(),
            IndustrySignalEvaluation.horizon_days.asc(),
        )
    )
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def delete_rows_by_source(
    db: AsyncSession,
    industry_key: str,
    sources: list[str],
    metric_keys: list[str] | None = None,
) -> int:
    """Purge source rows after real data lands so mock data cannot masquerade as real.

    ``metric_keys`` 给定时仅清除这些指标（按覆盖清除：未覆盖指标保留 mock 演示行）。
    """
    if not sources:
        return 0
    stmt = delete(IndustryMetric).where(
        IndustryMetric.industry_key == industry_key,
        IndustryMetric.stock_id == 0,
        IndustryMetric.source.in_(sources),
    )
    if metric_keys is not None:
        stmt = stmt.where(IndustryMetric.metric_key.in_(metric_keys))
    result = await db.execute(stmt)
    return result.rowcount or 0
