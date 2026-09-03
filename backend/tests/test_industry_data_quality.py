"""Pure unit tests for registry-driven industry data quality."""

from dataclasses import replace
from datetime import date
from types import SimpleNamespace

import pytest

from app.services.industry_data_quality import (
    MetricQualityResult,
    aggregate_industry_quality,
    assess_metric_quality,
    is_formal_signal_config_valid,
)
from app.services.industry_registry import (
    BROILER_INDUSTRY,
    PIG_INDUSTRY,
    SIGNAL_BUY,
    SIGNAL_SELL,
    TIER_HIGHFREQ,
    IndustryConfig,
    MetricDef,
    SignalVerificationConfig,
    VerificationHorizonDef,
    VerificationRuleDef,
)


def _result(metric_key: str, status: str = "ready") -> MetricQualityResult:
    return MetricQualityResult(
        metric_key=metric_key,
        status=status,
        source="derived",
        freq="daily",
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
    row = SimpleNamespace(
        source="akshare_soozhu", freq="weekly", period=date(2026, 8, 20), value=13.5
    )
    result = assess_metric_quality(metric, row, as_of=date(2026, 9, 3), for_signal=True)
    assert result.status == "stale"
    assert result.freq == "weekly"
    assert result.age_days == 14


def test_mock_source_is_rejected_for_formal_signal():
    metric = PIG_INDUSTRY.metric("hog_price")
    row = SimpleNamespace(source="mock", freq="daily", period=date(2026, 9, 3), value=13.5)
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


def test_none_value_is_missing_but_preserves_provenance():
    row = SimpleNamespace(source="derived", freq="monthly", period=date(2026, 9, 1), value=None)
    result = assess_metric_quality(
        PIG_INDUSTRY.metric("sow_inventory_mom"),
        row,
        as_of=date(2026, 9, 3),
        for_signal=True,
    )
    assert result.status == "missing"
    assert result.source == "derived"
    assert result.period == date(2026, 9, 1)
    assert result.age_days == 2


def test_none_required_value_makes_pig_unavailable():
    row = SimpleNamespace(source="derived", freq="monthly", period=date(2026, 9, 1), value=None)
    result = assess_metric_quality(
        PIG_INDUSTRY.metric("sow_inventory_mom"),
        row,
        as_of=date(2026, 9, 3),
        for_signal=True,
    )
    results = ready_pig_results_except()
    results = [
        result if item.metric_key == "sow_inventory_mom" else item
        for item in results
    ]
    quality = aggregate_industry_quality(PIG_INDUSTRY, results)
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_zero_value_is_not_missing():
    row = SimpleNamespace(source="derived", freq="monthly", period=date(2026, 9, 1), value=0.0)
    result = assess_metric_quality(
        PIG_INDUSTRY.metric("sow_inventory_mom"),
        row,
        as_of=date(2026, 9, 3),
        for_signal=True,
    )
    assert result.status == "ready"


def test_company_metric_below_minimum_coverage_is_partial():
    metric = replace(
        PIG_INDUSTRY.metric("company.hogs_sold_monthly"),
        coverage_scope="company",
        min_entity_coverage=0.8,
    )
    row = SimpleNamespace(source="manual", freq="monthly", period=date(2026, 9, 3), value=10.0)
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


def test_unconfigured_industry_defaults_to_demo_and_cannot_become_signal_ready():
    cfg = IndustryConfig(
        key="unconfigured",
        name="未配置行业",
        description="quality gate defaults must fail closed",
        sw_l3_codes=[],
        metrics=[
            MetricDef(
                key="price",
                name="价格",
                unit="元",
                freq="daily",
                tier=TIER_HIGHFREQ,
                sources=["real"],
            )
        ],
        phases=[],
        position_templates={},
    )
    quality = aggregate_industry_quality(cfg, [_result("price")])
    assert cfg.signal_quality_required is False
    assert quality.status == "demo"
    assert quality.signal_ready is False


def test_formal_config_without_complete_gate_declarations_fails_closed():
    cfg = replace(BROILER_INDUSTRY, signal_quality_required=True)
    quality = aggregate_industry_quality(cfg, demo_ready_results())
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_present_but_empty_verification_fails_closed():
    cfg = replace(
        PIG_INDUSTRY,
        verification=SignalVerificationConfig(
            methodology_version="",
            supported_signals=(),
            horizons=(),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False
    quality = aggregate_industry_quality(cfg, ready_pig_results_except())
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_malformed_horizon_rule_fails_closed():
    cfg = replace(
        PIG_INDUSTRY,
        verification=SignalVerificationConfig(
            methodology_version="pig-cycle-v1",
            supported_signals=(SIGNAL_BUY,),
            horizons=(
                VerificationHorizonDef(
                    days=30,
                    rules=(
                        VerificationRuleDef(
                            metric_key="unknown_metric",
                            direction="sideways",
                            threshold_pct=3.0,
                            weight=0,
                            grace_days=-1,
                        ),
                    ),
                ),
            ),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False
    quality = aggregate_industry_quality(cfg, ready_pig_results_except())
    assert quality.status == "unavailable"
    assert quality.signal_ready is False


def test_dashboard_only_rule_reference_is_rejected():
    verification = PIG_INDUSTRY.verification
    assert verification is not None
    first_horizon = verification.horizons[0]
    rules = (
        replace(first_horizon.rules[0], metric_key="industry_cost_avg"),
        *first_horizon.rules[1:],
    )
    cfg = replace(
        PIG_INDUSTRY,
        verification=replace(
            verification,
            horizons=(replace(first_horizon, rules=rules), *verification.horizons[1:]),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False


@pytest.mark.parametrize("threshold_pct", [None, 0.0, -3.0])
def test_buy_up_sell_down_rule_without_positive_threshold_is_rejected(threshold_pct):
    """Directional rules degrade to threshold 0 in scoring — must fail validation."""
    verification = PIG_INDUSTRY.verification
    assert verification is not None
    first_horizon = verification.horizons[0]
    rules = (
        replace(first_horizon.rules[0], threshold_pct=threshold_pct),
        *first_horizon.rules[1:],
    )
    cfg = replace(
        PIG_INDUSTRY,
        verification=replace(
            verification,
            horizons=(replace(first_horizon, rules=rules), *verification.horizons[1:]),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False


def test_horizon_missing_required_metric_is_rejected():
    verification = PIG_INDUSTRY.verification
    assert verification is not None
    first_horizon = verification.horizons[0]
    rules = (
        replace(first_horizon.rules[0], weight=50),
        replace(first_horizon.rules[1], weight=50),
    )
    cfg = replace(
        PIG_INDUSTRY,
        verification=replace(
            verification,
            horizons=(replace(first_horizon, rules=rules), *verification.horizons[1:]),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False


def test_duplicate_rule_metric_is_rejected():
    verification = PIG_INDUSTRY.verification
    assert verification is not None
    first_horizon = verification.horizons[0]
    rules = (
        first_horizon.rules[0],
        first_horizon.rules[1],
        replace(first_horizon.rules[2], metric_key="hog_price"),
    )
    cfg = replace(
        PIG_INDUSTRY,
        verification=replace(
            verification,
            horizons=(replace(first_horizon, rules=rules), *verification.horizons[1:]),
        ),
    )
    assert is_formal_signal_config_valid(cfg) is False


def test_pig_valid_config_and_ready_results_remain_signal_ready():
    assert is_formal_signal_config_valid(PIG_INDUSTRY) is True
    quality = aggregate_industry_quality(PIG_INDUSTRY, ready_pig_results_except())
    assert quality.status == "healthy"
    assert quality.signal_ready is True


def test_duplicate_metric_results_are_rejected():
    duplicate = _result("hog_price")
    with pytest.raises(ValueError, match="duplicate metric quality result: hog_price"):
        aggregate_industry_quality(PIG_INDUSTRY, [duplicate, duplicate])


def test_pig_registry_declares_quality_gate_and_verification_rules():
    assert PIG_INDUSTRY.signal_quality_required is True
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
    for horizon in verification.horizons:
        assert [rule.direction for rule in horizon.rules] == [
            "buy_up_sell_down",
            "buy_up_sell_down",
            "buy_lte_zero_sell_gte_zero",
        ]
    assert [sum(rule.weight for rule in horizon.rules) for horizon in verification.horizons] == [
        100,
        100,
    ]
