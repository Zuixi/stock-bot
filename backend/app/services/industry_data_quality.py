"""Pure registry-driven industry metric quality assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from app.services.industry_registry import IndustryConfig, MetricDef

_READY = "ready"


@dataclass(frozen=True)
class MetricQualityResult:
    metric_key: str
    status: str
    source: str | None
    period: date | None
    age_days: int | None
    reason: str | None
    entity_coverage: float | None


@dataclass(frozen=True)
class IndustryQualityResult:
    status: str
    signal_ready: bool
    ready_count: int
    missing_count: int
    stale_count: int
    rejected_count: int
    partial_count: int
    details: list[MetricQualityResult]


def assess_metric_quality(
    metric: MetricDef,
    row: Any | None,
    *,
    as_of: date,
    entity_coverage: float | None = None,
    for_signal: bool = False,
) -> MetricQualityResult:
    """Assess one already-selected metric row without performing I/O."""
    if row is None:
        return MetricQualityResult(
            metric_key=metric.key,
            status="missing",
            source=None,
            period=None,
            age_days=None,
            reason="no selected observation",
            entity_coverage=entity_coverage,
        )

    source = row.source
    period = row.period
    age_days = (as_of - period).days

    if row.value is None:
        return MetricQualityResult(
            metric_key=metric.key,
            status="missing",
            source=source,
            period=period,
            age_days=age_days,
            reason="selected observation has no value",
            entity_coverage=entity_coverage,
        )

    if metric.max_age_days is not None and age_days > metric.max_age_days:
        return MetricQualityResult(
            metric_key=metric.key,
            status="stale",
            source=source,
            period=period,
            age_days=age_days,
            reason=f"observation is older than {metric.max_age_days} days",
            entity_coverage=entity_coverage,
        )

    if for_signal and metric.allow_signal_sources and source not in metric.allow_signal_sources:
        return MetricQualityResult(
            metric_key=metric.key,
            status="source_rejected",
            source=source,
            period=period,
            age_days=age_days,
            reason=f"source {source!r} is not allowed for formal signals",
            entity_coverage=entity_coverage,
        )

    if (
        metric.coverage_scope == "company"
        and metric.min_entity_coverage is not None
        and (entity_coverage is None or entity_coverage < metric.min_entity_coverage)
    ):
        return MetricQualityResult(
            metric_key=metric.key,
            status="partial",
            source=source,
            period=period,
            age_days=age_days,
            reason=f"entity coverage is below {metric.min_entity_coverage:.0%}",
            entity_coverage=entity_coverage,
        )

    return MetricQualityResult(
        metric_key=metric.key,
        status=_READY,
        source=source,
        period=period,
        age_days=age_days,
        reason=None,
        entity_coverage=entity_coverage,
    )


def aggregate_industry_quality(
    cfg: IndustryConfig,
    results: list[MetricQualityResult],
) -> IndustryQualityResult:
    """Aggregate metric results according to registry dashboard and signal requirements."""
    by_key: dict[str, MetricQualityResult] = {}
    for result in results:
        if result.metric_key in by_key:
            raise ValueError(f"duplicate metric quality result: {result.metric_key}")
        by_key[result.metric_key] = result
    details = list(results)
    for metric in cfg.metrics:
        required = metric.required_for_dashboard or metric.required_for_signal
        if required and metric.key not in by_key:
            missing = MetricQualityResult(
                metric_key=metric.key,
                status="missing",
                source=None,
                period=None,
                age_days=None,
                reason="quality result was not provided",
                entity_coverage=None,
            )
            by_key[metric.key] = missing
            details.append(missing)

    required_signal_metrics = [metric for metric in cfg.metrics if metric.required_for_signal]
    formal_config_valid = (
        bool(required_signal_metrics)
        and cfg.verification is not None
        and all(metric.allow_signal_sources for metric in required_signal_metrics)
    )
    signal_ready = (
        cfg.signal_quality_required
        and formal_config_valid
        and all(by_key[metric.key].status == _READY for metric in required_signal_metrics)
    )
    dashboard_degraded = any(
        by_key[metric.key].status != _READY
        for metric in cfg.metrics
        if metric.required_for_dashboard
    )

    if not cfg.signal_quality_required:
        status = "demo"
        signal_ready = False
    elif not signal_ready:
        status = "unavailable"
    elif dashboard_degraded:
        status = "degraded"
    else:
        status = "healthy"

    counts = {quality_status: 0 for quality_status in (
        _READY,
        "missing",
        "stale",
        "source_rejected",
        "partial",
    )}
    for result in details:
        if result.status in counts:
            counts[result.status] += 1

    return IndustryQualityResult(
        status=status,
        signal_ready=signal_ready,
        ready_count=counts[_READY],
        missing_count=counts["missing"],
        stale_count=counts["stale"],
        rejected_count=counts["source_rejected"],
        partial_count=counts["partial"],
        details=details,
    )
