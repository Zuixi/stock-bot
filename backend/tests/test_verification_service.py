"""R4 跨源校验：判定逻辑与状态语义必须先红后绿（不依赖网络）."""

from __future__ import annotations

from datetime import date

from app.services import verification_service as svc

DAY = date(2026, 11, 10)


def _cmp(**kwargs):
    base = {
        "symbol": "600519",
        "local_close": 1439.0,
        "local_pct": -0.41,
        "remote_close": 1439.0,
        "remote_pct": -0.41,
    }
    base.update(kwargs)
    return svc.compare_row(**base)


def test_identical_values_match() -> None:
    assert _cmp().matched is True
    assert _cmp().reason is None


def test_small_floating_difference_match() -> None:
    assert _cmp(remote_close=1439.5).matched is True


def test_price_beyond_tolerance_is_mismatch() -> None:
    out = _cmp(remote_close=1300.0)
    assert out.matched is False
    assert out.reason and out.reason.startswith("close_diff=")


def test_pct_beyond_tolerance_is_mismatch() -> None:
    out = _cmp(remote_pct=3.0)
    assert out.matched is False
    assert out.reason and out.reason.startswith("pct_diff=")


def test_missing_remote_is_not_a_pass() -> None:
    out = _cmp(remote_close=None)
    assert out.matched is False
    assert out.reason == "missing_value"


def test_summarize_pass_warn_fail() -> None:
    good = [_cmp(symbol=f"60000{i}") for i in range(20)]
    assert svc.summarize(DAY, good, None).status == "pass"

    warn = [_cmp(symbol=f"60000{i}") for i in range(9)] + [
        _cmp(symbol=f"60001{i}", remote_close=1000.0) for i in range(1)
    ]
    assert svc.summarize(DAY, warn, None).status == "warn"

    bad = [_cmp(remote_close=1000.0) for _ in range(5)]
    assert svc.summarize(DAY, bad, None).status == "fail"


def test_source_unavailable_is_error_not_pass() -> None:
    out = svc.summarize(DAY, [], "eastmoney timeout")
    assert out.status == "error"
    assert out.ratio == 0.0
    assert out.detail["source_error"] == "eastmoney timeout"


def test_empty_sample_without_error_is_error() -> None:
    """没有任何可比值时不能"零比对零失败"地算通过。"""
    assert svc.summarize(DAY, [], None).status == "error"


def test_detail_lists_only_mismatches() -> None:
    out = svc.summarize(DAY, [_cmp(), _cmp(symbol="000001", remote_close=1.0)], None)
    mismatches = out.detail["mismatches"]
    assert len(mismatches) == 1
    assert mismatches[0]["symbol"] == "000001"
