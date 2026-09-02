"""纯单元测试：日度→月度 rollup 与 latest 频率裁决。"""

from datetime import date

from app.models.industry_research import IndustryMetric
from app.services.industry_metric_service import _pick_latest, _rollup_monthly_rows
from app.services.industry_registry import PIG_INDUSTRY


def _row(metric_key, source, period, freq="daily", value=1.0):
    return IndustryMetric(
        industry_key="pig", stock_id=0, metric_key=metric_key,
        source=source, freq=freq, period=period, value=value,
    )


def test_rollup_takes_last_daily_value_per_month():
    rows = [
        _row("hog_price", "akshare_soozhu", date(2026, 7, 10), value=10.0),
        _row("hog_price", "akshare_soozhu", date(2026, 7, 31), value=12.0),
        _row("hog_price", "akshare_soozhu", date(2026, 8, 5), value=13.0),
    ]
    m = PIG_INDUSTRY.metric("hog_price")
    out = _rollup_monthly_rows(PIG_INDUSTRY, m, rows)
    assert [(r["period"], r["value"], r["freq"]) for r in out] == [
        (date(2026, 7, 31), 12.0, "monthly"),
        (date(2026, 8, 31), 13.0, "monthly"),
    ]
    assert all(r["source"] == "akshare_soozhu" and r["source_tier"] == m.tier for r in out)


def test_rollup_marks_extra_and_skips_none_values():
    rows = [
        _row("hog_price", "akshare_soozhu", date(2026, 7, 10), value=None),
        _row("hog_price", "akshare_soozhu", date(2026, 7, 20), value=11.0),
    ]
    out = _rollup_monthly_rows(PIG_INDUSTRY, PIG_INDUSTRY.metric("hog_price"), rows)
    assert len(out) == 1
    assert out[0]["extra"] == {"rollup": "last_daily"}


def test_pick_latest_prefers_registry_freq_over_newer_other_freq():
    # hog_price 注册频率 daily：未来月末的 monthly 行不得压过当日 daily 行
    grouped = {
        "hog_price": [
            _row("hog_price", "mock", date(2026, 9, 30), freq="monthly"),
            _row("hog_price", "mock", date(2026, 9, 2), freq="daily"),
        ]
    }
    picked = _pick_latest(PIG_INDUSTRY, grouped, "hog_price")
    assert picked.freq == "daily" and picked.period == date(2026, 9, 2)


def test_pick_latest_falls_back_to_any_freq_when_registry_freq_absent():
    grouped = {
        "sow_inventory": [
            _row("sow_inventory", "stats_gov", date(2026, 6, 30), freq="monthly"),
        ]
    }
    assert _pick_latest(PIG_INDUSTRY, grouped, "sow_inventory").period == date(2026, 6, 30)
