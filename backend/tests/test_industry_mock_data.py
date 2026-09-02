"""纯单元测试：mock 批次与 industry_metrics 唯一约束（含 freq）的对齐。

同一 upsert 批次内若出现两条仅 freq 不同的同 key 行（月末 daily + monthly），
旧约束（不含 freq）会让 PostgreSQL 抛
"ON CONFLICT DO UPDATE command cannot affect row a second time" 并中断事务；
约束纳入 freq 后，跨频共存合法，但同 (metric_key, source, freq, period) 仍必须唯一。
"""

import calendar
import random

from app.services.industry_mock_data import _wobble_series, build_pig_mock_points


def test_mock_batch_has_no_duplicate_conflict_keys():
    """批级不变量：整批无重复 (metric_key, source, freq, period)，杜绝 PG 撞键。"""
    rows = build_pig_mock_points("pig", months=37)
    seen: set[tuple[str, str, str, object]] = set()
    for r in rows:
        key = (r["metric_key"], r["source"], r["freq"], r["period"])
        assert key not in seen, f"duplicate conflict key within one upsert batch: {key}"
        seen.add(key)


def test_mock_month_end_rows_legally_coexist_across_freqs():
    """月末日期可合法出现 daily + monthly 两行（freq 不同即不同冲突键），钉住新设计。"""
    rows = build_pig_mock_points("pig", months=37)
    freqs_by_key: dict[tuple[str, str, object], set[str]] = {}
    for r in rows:
        freqs_by_key.setdefault(
            (r["metric_key"], r["source"], r["period"]), set()
        ).add(r["freq"])

    crosses = {k: fs for k, fs in freqs_by_key.items() if len(fs) > 1}
    assert crosses, "expected month-end rows to appear under both daily and monthly freq"
    for metric_key, _source, period in crosses:
        # 跨频共存仅允许发生在日历月末（月度行 period=月末）
        assert period.day == calendar.monthrange(period.year, period.month)[1], (
            f"cross-freq coexistence on non-month-end {metric_key}@{period}"
        )
        assert freqs_by_key[(metric_key, _source, period)] == {"daily", "monthly"}
    # 日度窗口（近 45 天）必然覆盖上一个自然月末，hog_price/corn_price 必有跨频共存
    assert any(k[0] in ("hog_price", "corn_price") for k in crosses)


# ── months 回补窗口与抖动序列不变量（Task 4） ──────────────────────────


def test_wobble_series_exact_length_and_last_point():
    rng = random.Random(7)
    out = _wobble_series([3.0] * 45, rng, 0.01, 45)
    assert len(out) == 45
    assert out[-1] == 3.0  # 末点精确等于基准值


def test_mock_points_respects_months_window():
    rows = build_pig_mock_points("pig", months=12)
    monthly = [
        r for r in rows
        if r["metric_key"] == "hog_price" and r["freq"] == "monthly"
    ]
    assert len(monthly) == 12
    daily = [
        r for r in rows
        if r["metric_key"] == "hog_price" and r["freq"] == "daily"
    ]
    assert len(daily) <= 45


def test_mock_daily_last_equals_monthly_latest():
    rows = build_pig_mock_points("pig", months=37)
    daily = [r for r in rows if r["metric_key"] == "hog_price" and r["freq"] == "daily"]
    monthly = [r for r in rows if r["metric_key"] == "hog_price" and r["freq"] == "monthly"]
    assert daily[-1]["value"] == monthly[-1]["value"]
