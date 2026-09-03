"""Pure unit tests for registry-driven industry data quality."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

from app.services.industry_data_quality import (
    MetricQualityResult,
    aggregate_industry_quality,
    assess_metric_quality,
)
from app.services.industry_registry import (
    BROILER_INDUSTRY,
    PIG_INDUSTRY,
    SIGNAL_BUY,
    SIGNAL_SELL,
)


def _result(metric_key: str, status: str = "ready") -> MetricQualityResult:
    return MetricQualityResult(
        metric_key=metric_key,
        status=status,
        source="derived",
        period=date(2026, 9, 3),
        age_days=0,
        reason=None,
        entity_coverage=None,
    )


def ready_pig_results_except(
    metric_key: str | None = None,
    *,
    status: str = "ready",
) -> list[MetricQualityResult]:
    results = [_result(metric.key) for metric in PIG_INDUSTRY.metrics]
    if metric_key is not None:
        results = [
            replace(result, status=status) if result.metric_key == metric_key else result
            for result in results
        ]
    return results


def demo_ready_results() -> list[MetricQualityResult]:
    return [_result(metric.key) for metric in BROILER_INDUSTRY.metrics]


def test_daily_metric_becomes_stale_after_max_age():
    metric = replace(PIG_INDUSTRY.metric("hog_price"), max_age_days=7)
    row = SimpleNamespace(source="akshare_soozhu", period=date(2026, 8, 20), value=13.5)
    result = assess_metric_quality(metric, row, as_of=date(2026, 9, 3), for_signal=True)
    assert result.status == "stale"
    assert result.age_days == 14


def test_mock_source_is_rejected_for_formal_signal():
    metric = PIG_INDUSTRY.metric("hog_price")
    row = SimpleNamespace(source="mock", period=date(2026, 9, 3), value=13.5)
    result = assess_metric_quality(metric, row, as_of=date(2026, 9, 3), for_signal=True)
    assert result.status == "source_rejected"


def test_missing_required_signal_metric_makes_signal_unavailable():
    results = ready_pig_results_except("sow_inventory_mom", status="missing")
    quality = aggregate_industry_quality(PIG_INDUSTRY, results)
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_broiler_is_explicit_demo_not_formal_ready():
    quality = aggregate_industry_quality(BROILER_INDUSTRY, demo_ready_results())
    assert quality.status == "demo"
    assert quality.signal_ready is False


def test_missing_row_is_reported_without_provenance():
    result = assess_metric_quality(
        PIG_INDUSTRY.metric("hog_price"),
        None,
        as_of=date(2026, 9, 3),
        for_signal=True,
    )
    assert result.status == "missing"
    assert result.source is None
    assert result.period is None
    assert result.age_days is None


def test_company_metric_below_minimum_coverage_is_partial():
    metric = replace(
        PIG_INDUSTRY.metric("company.hogs_sold_monthly"),
        coverage_scope="company",
        min_entity_coverage=0.8,
    )
    row = SimpleNamespace(source="manual", period=date(2026, 9, 3), value=10.0)
    result = assess_metric_quality(
        metric,
        row,
        as_of=date(2026, 9, 3),
        entity_coverage=0.75,
    )
    assert result.status == "partial"
    assert result.entity_coverage == 0.75


def test_dashboard_only_problem_degrades_without_blocking_signal():
    results = ready_pig_results_except("industry_cost_avg", status="missing")
    quality = aggregate_industry_quality(PIG_INDUSTRY, results)
    assert quality.status == "degraded"
    assert quality.signal_ready is True
    assert quality.missing_count == 1


def test_missing_required_result_is_synthesized_as_unavailable():
    results = [
        result
        for result in ready_pig_results_except()
        if result.metric_key != "sow_inventory_mom"
    ]
    quality = aggregate_industry_quality(PIG_INDUSTRY, results)
    assert quality.status == "unavailable"
    assert quality.missing_count == 1
    assert any(
        result.metric_key == "sow_inventory_mom" and result.status == "missing"
        for result in quality.details
    )


def test_pig_registry_declares_quality_gate_and_verification_rules():
    for key in ("hog_price", "hog_corn_ratio"):
        metric = PIG_INDUSTRY.metric(key)
        assert metric.required_for_dashboard is True
        assert metric.required_for_signal is True
        assert metric.max_age_days == 7
        assert "mock" not in metric.allow_signal_sources

    sow_mom = PIG_INDUSTRY.metric("sow_inventory_mom")
    assert sow_mom.required_for_dashboard is True
    assert sow_mom.required_for_signal is True
    assert sow_mom.max_age_days == 75
    assert "mock" not in sow_mom.allow_signal_sources
    assert PIG_INDUSTRY.metric("industry_cost_avg").required_for_signal is False

    verification = PIG_INDUSTRY.verification
    assert verification is not None
    assert verification.methodology_version == "pig-cycle-v1"
    assert verification.supported_signals == (SIGNAL_BUY, SIGNAL_SELL)
    assert [horizon.days for horizon in verification.horizons] == [30, 90]
    assert [sum(rule.weight for rule in horizon.rules) for horizon in verification.horizons] == [
        100,
        100,
    ]
