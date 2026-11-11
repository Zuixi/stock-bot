"""P5 期望断言：注册表完整性 + 比较方向 + 阻断判定（纯函数，不连库）。"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.data_expectations import (
    EXPECTATIONS,
    ExpectationResult,
    _status_for,
    has_blocking_failure,
)


def test_registry_has_eleven_unique_well_formed_entries() -> None:
    assert len(EXPECTATIONS) == 11
    keys = [item.key for item in EXPECTATIONS]
    assert len(keys) == len(set(keys))
    for item in EXPECTATIONS:
        assert item.key.startswith("dq.")
        assert item.severity in ("block", "warn")
        assert item.compare in ("gte", "lte", "eq")
        # 按日断言必须带 :day；申万一级数是对"当前快照"的检查，不按日。
        if item.key != "dq.sw.level1_count":
            assert ":day" in item.sql
        assert item.description


def test_price_identity_is_a_blocking_local_check() -> None:
    """上游把 close 写错时，行数/非空率/区间都可能达标，只有这条恒等式会红。"""
    item = next(i for i in EXPECTATIONS if i.key == "dq.daily_quotes.price_identity")
    assert item.severity == "block"
    assert item.compare == "lte"
    assert "close / pre_close" in item.sql
    assert "pct_chg" in item.sql


@pytest.mark.parametrize(
    ("compare", "severity", "observed", "threshold", "expected"),
    [
        ("gte", "block", 0.95, 0.9, "pass"),
        ("gte", "block", 0.5, 0.9, "fail"),
        ("gte", "warn", 0.5, 0.9, "warn"),
        ("lte", "block", 0.0, 0.0, "pass"),
        ("lte", "block", 1.0, 0.0, "fail"),
        ("eq", "warn", 31.0, 31.0, "pass"),
        ("eq", "warn", 30.0, 31.0, "warn"),
    ],
)
def test_status_matrix(
    compare: str, severity: str, observed: float, threshold: float, expected: str
) -> None:
    from app.services.data_expectations import Expectation

    item = Expectation(
        key="dq.x",
        domain="x",
        severity=severity,  # type: ignore[arg-type]
        compare=compare,  # type: ignore[arg-type]
        description="x",
        sql="SELECT 1, 1",
    )
    assert _status_for(item, observed, threshold) == expected


def test_null_observed_fails_block_but_only_warns_on_warn() -> None:
    from app.services.data_expectations import Expectation

    block = Expectation(
        key="dq.b",
        domain="x",
        severity="block",
        compare="gte",
        description="x",
        sql="SELECT 1, 1",
    )
    warn = Expectation(
        key="dq.w",
        domain="x",
        severity="warn",
        compare="gte",
        description="x",
        sql="SELECT 1, 1",
    )
    assert _status_for(block, None, 1.0) == "fail"
    assert _status_for(warn, None, 1.0) == "warn"


def test_blocking_failure_only_counts_block_severity() -> None:
    def result(key: str, severity: str, status: str) -> ExpectationResult:
        return ExpectationResult(
            key=key,
            domain="d",
            severity=severity,  # type: ignore[arg-type]
            status=status,  # type: ignore[arg-type]
            observed=0.0,
            threshold=1.0,
            business_date=date(2026, 11, 10),
            description="x",
        )

    assert has_blocking_failure([result("a", "block", "fail")])
    assert not has_blocking_failure([result("b", "warn", "fail")])
    assert not has_blocking_failure([result("c", "block", "pass")])
