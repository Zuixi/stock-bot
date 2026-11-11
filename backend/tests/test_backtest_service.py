"""S2.3 回测契约：参数/切分/PIT 是纯函数，必须先红后绿."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.services import backtest_service as svc


def _split(**over):
    base = {
        "in_sample": {"from": "2026-01-01", "to": "2026-06-30"},
        "out_of_sample": {"from": "2026-07-01", "to": "2026-12-31"},
    }
    base.update(over)
    return base


def test_validate_accepts_adjacent_split():
    svc.validate_request(
        scope_key="pig",
        window_from=date(2026, 1, 1),
        window_to=date(2026, 12, 31),
        horizons=[1, 5, 20],
        split=_split(),
    )


def test_validate_rejects_overlap():
    with pytest.raises(svc.BacktestError, match="重叠"):
        svc.validate_request(
            scope_key="pig",
            window_from=date(2026, 1, 1),
            window_to=date(2026, 12, 31),
            horizons=[1],
            split=_split(
                in_sample={"from": "2026-01-01", "to": "2026-08-01"},
            ),
        )


def test_validate_rejects_non_industry_scope():
    with pytest.raises(svc.BacktestError, match="不是已登记"):
        svc.validate_request(
            scope_key="market",
            window_from=date(2026, 1, 1),
            window_to=date(2026, 12, 31),
            horizons=[1],
            split=_split(),
        )


def test_validate_rejects_split_outside_window():
    with pytest.raises(svc.BacktestError, match="落在回测窗口内"):
        svc.validate_request(
            scope_key="pig",
            window_from=date(2026, 2, 1),
            window_to=date(2026, 12, 31),
            horizons=[1],
            split=_split(),
        )


def test_summarize_empty_is_not_zero():
    out = svc.summarize([], [])
    assert out["n"] == 0
    assert out["mean"] is None
    assert out["insufficient_sample"] is True


def test_summarize_flagged_under_30():
    out = svc.summarize([0.1, -0.2], [0.05, -0.1])
    assert out["n"] == 2
    assert out["insufficient_sample"] is True
    assert out["win_rate"] == pytest.approx(0.5)
    assert out["excess_mean"] == pytest.approx(-0.025)


def test_pit_rejects_late_created_signal():
    late = svc.Event(
        day=date(2026, 1, 5),
        created_at=datetime(2026, 1, 10, tzinfo=UTC),
    )
    on_time = svc.Event(
        day=date(2026, 1, 5),
        created_at=datetime(2026, 1, 5, 20, tzinfo=UTC),
    )
    assert svc._pit_violations([on_time]) == []
    assert svc._pit_violations([late]) == [date(2026, 1, 5)]


def test_pit_allows_next_day_generation():
    # 日结任务在 T+1 凌晨生成 T 日信号是正常调度，不算回填。
    signal = svc.Event(
        day=date(2026, 1, 5),
        created_at=datetime(2026, 1, 6, 1, tzinfo=UTC),
    )
    assert svc._pit_violations([signal]) == []


def test_pit_boundary_is_strictly_after_plus_one_day():
    signal = svc.Event(
        day=date(2026, 1, 5),
        created_at=datetime(2026, 1, 6, tzinfo=UTC) + timedelta(days=1),
    )
    assert svc._pit_violations([signal]) == [date(2026, 1, 5)]
