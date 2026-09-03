"""Quality-gated industry signals, immutable transitions, and deterministic verification."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.industry_research import IndustrySignalEvent
from app.repositories import industry_metric_repo as repo
from app.services import cycle_engine
from app.services.industry_data_quality import (
    IndustryQualityResult,
    aggregate_industry_quality,
    assess_metric_quality,
)
from app.services.industry_registry import (
    SIGNAL_BUY,
    IndustryConfig,
    VerificationRuleDef,
)


@dataclass(frozen=True)
class CycleSnapshot:
    cycle_input: cycle_engine.CycleInput
    basis_periods: dict[str, str]


@dataclass(frozen=True)
class SignalUpdateResult:
    signal: Any | None
    updated: bool
    stale: bool
    event: IndustrySignalEvent | None


@dataclass(frozen=True)
class VerificationScore:
    status: str
    score: Decimal | None
    criteria_results: list[dict[str, Any]]
    insufficient_reasons: list[str]


@dataclass(frozen=True)
class EvaluationRunResult:
    due: int
    evaluated: int
    pending: int
    confirmed: int
    partially_confirmed: int
    invalidated: int
    inconclusive: int


def _quality_payload(quality: IndustryQualityResult, *, as_of: date) -> dict[str, Any]:
    details = []
    for result in quality.details:
        item = asdict(result)
        if result.period is not None:
            item["period"] = result.period.isoformat()
        details.append(item)
    return {
        "industry_key": None,
        "as_of": as_of,
        "status": quality.status,
        "signal_ready": quality.signal_ready,
        "ready_count": quality.ready_count,
        "missing_count": quality.missing_count,
        "stale_count": quality.stale_count,
        "rejected_count": quality.rejected_count,
        "partial_count": quality.partial_count,
        "details": details,
    }


async def assess_current_quality(
    db: AsyncSession, cfg: IndustryConfig, *, as_of: date
) -> IndustryQualityResult:
    """Select the same rows as signal generation, assess them, and persist the snapshot."""
    from app.services.industry_metric_service import _pick_row

    grouped = await repo.latest_rows_by_metric(db, cfg.key)
    results = []
    for metric in cfg.metrics:
        selected = _pick_row(metric, grouped.get(metric.key, []))
        results.append(
            assess_metric_quality(
                metric,
                selected,
                as_of=as_of,
                for_signal=metric.required_for_signal,
            )
        )
    quality = aggregate_industry_quality(cfg, results)
    payload = _quality_payload(quality, as_of=as_of)
    payload["industry_key"] = cfg.key
    await repo.upsert_quality_snapshot(db, payload)
    return quality


async def _build_cycle_snapshot(db: AsyncSession, cfg: IndustryConfig) -> CycleSnapshot:
    from app.services.industry_metric_service import _pick_row

    grouped = await repo.latest_rows_by_metric(db, cfg.key)
    selected = {
        key: _pick_row(cfg.metric(key), grouped.get(key, []))
        for key in ("hog_corn_ratio", "hog_price", "industry_cost_avg", "sow_inventory_mom")
    }

    async def series(metric_key: str, limit: int) -> list[float]:
        row = selected[metric_key]
        metric = cfg.metric(metric_key)
        if row is None or metric is None:
            return []
        rows = await repo.get_metric_history(
            db,
            cfg.key,
            metric_key,
            limit=limit,
            freq=metric.freq,
            source=row.source,
        )
        return [float(item.value) for item in rows if item.value is not None]

    ratio_row = selected["hog_corn_ratio"]
    price_row = selected["hog_price"]
    cost_row = selected["industry_cost_avg"]
    periods = {
        key: row.period.isoformat()
        for key, row in selected.items()
        if row is not None and row.value is not None
    }
    return CycleSnapshot(
        cycle_input=cycle_engine.CycleInput(
            ratio=float(ratio_row.value)
            if ratio_row is not None and ratio_row.value is not None
            else None,
            price=float(price_row.value)
            if price_row is not None and price_row.value is not None
            else None,
            cost=float(cost_row.value)
            if cost_row is not None and cost_row.value is not None
            else None,
            sow_mom_series=await series("sow_inventory_mom", 12),
            ratio_series=await series("hog_corn_ratio", 30),
        ),
        basis_periods=periods,
    )


def _event_start_snapshot(
    signal_row: Any,
    basis_periods: dict[str, str],
    quality: IndustryQualityResult,
) -> dict[str, Any]:
    basis = signal_row.basis or {}
    metrics: dict[str, dict[str, Any]] = {}
    sources = {item.metric_key: item.source for item in quality.details}
    values = {
        "hog_corn_ratio": basis.get("ratio"),
        "hog_price": basis.get("price"),
        "sow_inventory_mom": (basis.get("sow_mom_series") or [None])[-1],
    }
    for metric_key, value in values.items():
        period = basis_periods.get(metric_key)
        if value is not None and period is not None:
            metrics[metric_key] = {
                "value": value,
                "period": period,
                "source": sources.get(metric_key),
            }
    return {"signal_type": signal_row.signal_type, "metrics": metrics}


async def ensure_signal_event(
    db: AsyncSession,
    cfg: IndustryConfig,
    signal_row: Any,
    *,
    basis_periods: dict[str, str],
    quality: IndustryQualityResult,
) -> IndustrySignalEvent | None:
    """Create a baseline/transition event and its frozen pending evaluations."""
    verification = cfg.verification
    if verification is None:
        return None
    previous = await repo.latest_signal_event(db, cfg.key)
    if (
        previous is not None
        and previous.signal_type == signal_row.signal_type
        and previous.phase == signal_row.phase
    ):
        return None

    quality_snapshot = _quality_payload(quality, as_of=signal_row.effective_date)
    quality_snapshot.pop("industry_key")
    event = await repo.create_signal_event(
        db,
        {
            "industry_key": cfg.key,
            "event_date": signal_row.effective_date,
            "previous_signal_type": previous.signal_type if previous else None,
            "previous_phase": previous.phase if previous else None,
            "signal_type": signal_row.signal_type,
            "phase": signal_row.phase,
            "basis": signal_row.basis or {},
            "basis_periods": basis_periods,
            "quality_snapshot": quality_snapshot,
            "rule_version": verification.methodology_version,
        },
    )
    if event is None or signal_row.signal_type not in verification.supported_signals:
        return event

    start_snapshot = _event_start_snapshot(signal_row, basis_periods, quality)
    for horizon in verification.horizons:
        await repo.upsert_signal_evaluation(
            db,
            {
                "signal_event_id": event.id,
                "horizon_days": horizon.days,
                "methodology_version": verification.methodology_version,
                "target_date": signal_row.effective_date + timedelta(days=horizon.days),
                "status": "pending",
                "rules": [asdict(rule) for rule in horizon.rules],
                "start_snapshot": start_snapshot,
                "end_snapshot": None,
                "criteria_results": None,
                "insufficient_reasons": None,
                "score": None,
                "evaluated_at": None,
            },
        )
    return event


async def evaluate_and_store_signal(
    db: AsyncSession,
    cfg: IndustryConfig,
    *,
    quality: IndustryQualityResult,
    effective_date: date,
) -> SignalUpdateResult:
    """Gate formal signals before invoking the pure cycle engine."""
    if cfg.signal_quality_required and not quality.signal_ready:
        previous = await repo.latest_signal(db, cfg.key)
        return SignalUpdateResult(
            signal=previous,
            updated=False,
            stale=True,
            event=None,
        )

    snapshot = await _build_cycle_snapshot(db, cfg)
    output = cycle_engine.evaluate_pig_cycle(snapshot.cycle_input, cfg)
    signal = await repo.upsert_signal(
        db,
        {
            "industry_key": cfg.key,
            "phase": output.phase,
            "signal_type": output.signal,
            "positions": [asdict(item) for item in output.positions],
            "reason": "；".join(output.reasons),
            "basis": output.basis,
            "effective_date": effective_date,
        },
    )
    event = await ensure_signal_event(
        db,
        cfg,
        signal,
        basis_periods=snapshot.basis_periods,
        quality=quality,
    )
    return SignalUpdateResult(signal=signal, updated=True, stale=False, event=event)


def _rule_from(value: VerificationRuleDef | dict[str, Any]) -> VerificationRuleDef:
    return value if isinstance(value, VerificationRuleDef) else VerificationRuleDef(**value)


def _metric(snapshot: dict[str, Any], metric_key: str) -> dict[str, Any] | None:
    value = snapshot.get("metrics", {}).get(metric_key)
    return value if isinstance(value, dict) else None


def score_verification(
    rules: Iterable[VerificationRuleDef | dict[str, Any]],
    start_snapshot: dict[str, Any],
    end_snapshot: dict[str, Any],
) -> VerificationScore:
    """Score frozen industry-metric evidence with buy/sell-dependent directions."""
    signal_type = start_snapshot.get("signal_type")
    criteria: list[dict[str, Any]] = []
    insufficient: list[str] = []
    total = Decimal("0")

    for raw_rule in rules:
        rule = _rule_from(raw_rule)
        start = _metric(start_snapshot, rule.metric_key)
        end = _metric(end_snapshot, rule.metric_key)
        if start is None or end is None or start.get("value") is None or end.get("value") is None:
            if rule.required:
                insufficient.append(f"{rule.metric_key}: missing required evidence")
            criteria.append({"metric_key": rule.metric_key, "status": "missing", "score": None})
            continue

        start_value = float(start["value"])
        end_value = float(end["value"])
        awarded = Decimal("0")
        outcome = "failed"
        change_pct: float | None = None
        if rule.direction == "buy_up_sell_down":
            if start_value == 0:
                if rule.required:
                    insufficient.append(f"{rule.metric_key}: zero start value")
                criteria.append({"metric_key": rule.metric_key, "status": "missing", "score": None})
                continue
            change_pct = (end_value - start_value) / abs(start_value) * 100
            directed_change = change_pct if signal_type == SIGNAL_BUY else -change_pct
            threshold = rule.threshold_pct or 0.0
            if directed_change >= threshold:
                awarded = Decimal(rule.weight)
                outcome = "met"
            elif directed_change > -threshold:
                awarded = Decimal(rule.weight) / Decimal("2")
                outcome = "neutral"
        elif rule.direction == "buy_lte_zero_sell_gte_zero":
            met = end_value <= 0 if signal_type == SIGNAL_BUY else end_value >= 0
            if met:
                awarded = Decimal(rule.weight)
                outcome = "met"
        total += awarded
        criteria.append(
            {
                "metric_key": rule.metric_key,
                "status": outcome,
                "weight": rule.weight,
                "score": str(awarded),
                "start_value": start_value,
                "end_value": end_value,
                "change_pct": change_pct,
            }
        )

    if insufficient:
        return VerificationScore("inconclusive", None, criteria, insufficient)
    status = (
        "confirmed"
        if total >= 70
        else "partially_confirmed"
        if total >= 40
        else "invalidated"
    )
    return VerificationScore(status, total, criteria, [])


def _first_eligible(rows: list[Any], target_date: date, deadline: date) -> Any | None:
    return next(
        (
            row
            for row in rows
            if row.value is not None and target_date <= row.period <= deadline
        ),
        None,
    )


async def run_due_signal_evaluations(
    db: AsyncSession, cfg: IndustryConfig, *, as_of: date
) -> EvaluationRunResult:
    """Evaluate pending records from their frozen rules and first eligible observations."""
    due = await repo.list_due_signal_evaluations(db, cfg.key, as_of)
    counts = {
        "evaluated": 0,
        "pending": 0,
        "confirmed": 0,
        "partially_confirmed": 0,
        "invalidated": 0,
        "inconclusive": 0,
    }
    for evaluation in due:
        event = await db.get(IndustrySignalEvent, evaluation.signal_event_id)
        if event is None:
            counts["pending"] += 1
            continue

        end_metrics: dict[str, dict[str, Any]] = {}
        waiting = False
        for raw_rule in evaluation.rules:
            rule = _rule_from(raw_rule)
            metric = cfg.metric(rule.metric_key)
            start_metric = _metric(evaluation.start_snapshot, rule.metric_key)
            frozen_source = start_metric.get("source") if start_metric else None
            rows = await repo.get_metric_history(
                db,
                cfg.key,
                rule.metric_key,
                limit=4000,
                freq=metric.freq if metric else None,
                source=frozen_source,
            )
            deadline = evaluation.target_date + timedelta(days=rule.grace_days)
            selected = _first_eligible(rows, evaluation.target_date, deadline)
            if selected is not None:
                end_metrics[rule.metric_key] = {
                    "value": float(selected.value),
                    "period": selected.period.isoformat(),
                    "source": selected.source,
                }
            elif as_of <= deadline:
                waiting = True
        if waiting:
            counts["pending"] += 1
            continue

        end_snapshot = {"signal_type": event.signal_type, "metrics": end_metrics}
        scored = score_verification(evaluation.rules, evaluation.start_snapshot, end_snapshot)
        await repo.upsert_signal_evaluation(
            db,
            {
                "signal_event_id": evaluation.signal_event_id,
                "horizon_days": evaluation.horizon_days,
                "methodology_version": evaluation.methodology_version,
                "target_date": evaluation.target_date,
                "status": scored.status,
                "rules": evaluation.rules,
                "start_snapshot": evaluation.start_snapshot,
                "end_snapshot": end_snapshot,
                "criteria_results": scored.criteria_results,
                "insufficient_reasons": scored.insufficient_reasons or None,
                "score": scored.score,
                "evaluated_at": datetime.now(UTC),
            },
        )
        counts["evaluated"] += 1
        counts[scored.status] += 1

    return EvaluationRunResult(due=len(due), **counts)
