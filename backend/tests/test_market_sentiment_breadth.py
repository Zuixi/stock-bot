"""spec §S1.8 情绪/市场宽度与动量 —— 纯单测（不触 DB / 不触 Redis）。

覆盖三类不变量：

1. **6 个 `metric_key` 的登记一致性**：registry ↔ 服务的单位投影（字典 §4.2 规则 3：
   单位与频率随 key 固定在 registry，服务层不得各写一套）。
2. **缺数恒 `None`、绝不 0**（spec §S1.8 验收）：`_ratio` / `_pct` / 无底座行 /
   无行情行 / 无 20 日窗口 五条缺数路径。
3. **同源对拍**：`breadth_counts_from_rows` 与市场模块 `_distribution_bucket` 的
   11 桶**逐桶**对拍；`up - up_sign_count == flat_in_positive_bucket` 恒等式锁定
   （详见服务模块里 `DISTRIBUTION_UP_LABELS` 的口径说明）。

真库用例在 `tests/test_market_sentiment_breadth_db.py`（事务回滚，不污染库）。
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import market_sentiment_service as svc
from app.services.industry_registry import (
    INDUSTRIES,
    MARKET_CALIBER_VERSION,
    MARKET_METRICS,
    MARKET_SENTIMENT,
    MARKET_SENTIMENT_SOURCE,
)
from app.services.market_service import _DISTRIBUTION_RANGES, _distribution_bucket

S18_KEYS = (
    "advance_decline_ratio",
    "limit_up_down_ratio",
    "above_ma20_ratio",
    "limit_up_break_ratio",
    "max_consecutive_limit_up",
    "first_board_promotion_ratio",
)


# ── 1) 登记一致性 ─────────────────────────────────────────────────────


def test_market_scope_registered_with_s18_metrics() -> None:
    """'market' scope 在 registry 里，且 6 个 S1.8 指标一个不少。"""
    assert INDUSTRIES["market"] is MARKET_SENTIMENT
    keys = {m.key for m in MARKET_METRICS}
    assert set(S18_KEYS) <= keys
    assert keys == set(svc.ALL_METRIC_KEYS), "registry 与服务的 key 集必须逐字一致"


def test_every_metric_is_daily_derived_local_calc() -> None:
    """8 个指标都是日频 / 派生 tier / 源只有 local_calc（单一源，序列不跨源拼接）。"""
    for m in MARKET_METRICS:
        assert m.freq == "daily", m.key
        assert m.tier == "derived", m.key
        assert m.sources == [MARKET_SENTIMENT_SOURCE], m.key
        assert m.caliber_version == MARKET_CALIBER_VERSION, m.key


def test_service_unit_projection_matches_registry() -> None:
    """服务的 `_UNITS` 必须是 registry `MetricDef.unit` 的逐字投影（空串 → None）。"""
    registry_units = {m.key: (m.unit or None) for m in MARKET_METRICS}
    assert svc._UNITS == registry_units


def test_registry_description_documents_definitions() -> None:
    """spec §S1.8 行为判据"连板高度与首板晋级率的定义在 UI 可查"。

    前端页面归后续批次，但**定义数据必须在 registry 里可查**（读面 `description`
    字段直接下发）。这条用例把两个定义的关键措辞钉住，防止以后被改成空描述。
    """
    by_key = {m.key: m for m in MARKET_METRICS}
    assert "首日为 1 板" in by_key["max_consecutive_limit_up"].description
    assert "昨日首板" in by_key["first_board_promotion_ratio"].description
    # 定义数据里必须能查到分母（可复核性），不能只给一个比值
    assert "promo_1to2_n" in by_key["first_board_promotion_ratio"].description


def test_find_metric_resolves_market_keys() -> None:
    """通用读面靠 `find_metric` 做白名单：8 个 key 必须都能解析到 'market' scope。"""
    from app.services.industry_registry import find_metric

    for key in svc.ALL_METRIC_KEYS:
        found = find_metric(key)
        assert found is not None, key
        assert found[0] == "market", key


# ── 2) 缺数恒 None ────────────────────────────────────────────────────


def test_published_at_is_close_time_in_shanghai() -> None:
    """`published_at` = 交易日 15:00 Asia/Shanghai（带时区，non-naive）。"""
    got = svc.published_at_for(date(2026, 10, 9))
    assert got == datetime(2026, 10, 9, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert got.utcoffset() is not None and got.utcoffset().total_seconds() == 8 * 3600


@pytest.mark.parametrize(
    ("numerator", "denominator", "expected"),
    [
        (3297, 2145, 3297 / 2145),
        (1, 0, None),  # 分母 0 → 无定义，绝不 inf / 0
        (0, 5, 0.0),  # 真 0 是事实断言，保留
        (None, 5, None),
        (5, None, None),
        (None, None, None),
    ],
)
def test_ratio_never_fabricates(
    numerator: float | None, denominator: float | None, expected: float | None
) -> None:
    assert svc._ratio(numerator, denominator) == expected


def test_pct_passthrough_none() -> None:
    assert svc._pct(None) is None
    assert svc._pct(0.2174) == pytest.approx(21.74)
    assert svc._pct(0.0) == 0.0


def test_sentiment_metrics_without_base_row_are_none_not_zero() -> None:
    """底座无行 → 4 个涨停类指标全 `None` + `no_sentiment_day`，**不是 0**。

    0 在"涨跌停家数"上是"今天只有 0 家涨停"这一事实断言，与"这天没数据"相反。
    """
    out = {m.metric_key: m for m in svc._sentiment_metrics(date(2026, 1, 5), None)}
    assert set(out) == {
        "limit_up_down_ratio",
        "limit_up_break_ratio",
        "max_consecutive_limit_up",
        "first_board_promotion_ratio",
    }
    for m in out.values():
        assert m.value is None
        assert m.missing_reason == svc.MISSING_NO_SENTIMENT_DAY


def test_sentiment_metrics_project_base_columns_verbatim() -> None:
    """有底座行 → 只做除法 / ×100 / 投影，数值与底座列一一对应。"""

    class Row:
        zt_count = 72
        dt_count = 11
        broken_rate = 0.2174
        promo_1to2 = 0.22857142857142856
        promo_1to2_n = 35
        max_streak = 9
        max_streak_symbol = "600825"
        source = "local_calc"

    out = {m.metric_key: m for m in svc._sentiment_metrics(date(2026, 10, 9), Row())}
    assert out["limit_up_down_ratio"].value == pytest.approx(72 / 11)
    assert out["limit_up_down_ratio"].extra["numerator"] == 72
    assert out["limit_up_down_ratio"].extra["denominator"] == 11
    assert out["limit_up_break_ratio"].value == pytest.approx(21.74)
    assert out["first_board_promotion_ratio"].value == pytest.approx(22.857142857, rel=1e-9)
    assert out["first_board_promotion_ratio"].extra["denominator"] == 35
    assert out["max_consecutive_limit_up"].value == 9.0
    assert out["max_consecutive_limit_up"].extra["max_streak_symbol"] == "600825"
    for m in out.values():
        assert m.missing_reason is None


def test_sentiment_metrics_zero_promotion_denominator_is_none() -> None:
    """首板晋级率分母为 0 → `None`（比值无定义），即使上游误写了 0.0。"""

    class Row:
        zt_count = 10
        dt_count = 3
        broken_rate = 0.5
        promo_1to2 = 0.0
        promo_1to2_n = 0
        max_streak = 2
        max_streak_symbol = "000001"
        source = "local_calc"

    out = {m.metric_key: m for m in svc._sentiment_metrics(date(2026, 1, 5), Row())}
    assert out["first_board_promotion_ratio"].value is None
    assert out["first_board_promotion_ratio"].missing_reason == svc.MISSING_SOURCE_NULL
    assert out["first_board_promotion_ratio"].extra["denominator"] == 0


def test_breadth_metrics_without_quote_rows_are_none_not_zero() -> None:
    """行情无行 → 涨跌家数与涨跌比全 `None` + `no_quote_rows`，**不是 0**。"""
    out = {m.metric_key: m for m in svc._breadth_metrics(None)}
    assert set(out) == {"market_advance_count", "market_decline_count", "advance_decline_ratio"}
    for m in out.values():
        assert m.value is None
        assert m.missing_reason == svc.MISSING_NO_QUOTE_ROWS


def test_ma20_metric_missing_window_is_none_not_zero() -> None:
    """该日不在 MA20 结果里 → `None` + `no_ma20_window`（**不是 0%**）。"""
    m = svc._ma20_metric(date(2026, 1, 5), {})
    assert m.value is None
    assert m.missing_reason == svc.MISSING_NO_MA20_WINDOW
    assert m.extra["window"] == svc.MA20_WINDOW


def test_ma20_metric_ratio_and_extra() -> None:
    """`above / eligible × 100`，分子分母都进 `extra`（可手算复核）。"""
    m = svc._ma20_metric(date(2026, 10, 9), {date(2026, 10, 9): (5542, 2146)})
    assert m.value == pytest.approx(2146 / 5542 * 100)
    assert m.extra == {"window": 20, "eligible": 5542, "above": 2146}
    assert m.missing_reason is None


def test_ma20_metric_zero_eligible_is_none() -> None:
    """eligible = 0 → `None`（防御性：GROUP BY 不会产出 0 行的组，但分母为 0 不许落 0）。"""
    m = svc._ma20_metric(date(2026, 10, 9), {date(2026, 10, 9): (0, 0)})
    assert m.value is None and m.missing_reason == svc.MISSING_NO_MA20_WINDOW


# ── 3) 同源：与市场分布逐桶对拍 ───────────────────────────────────────


def _rows(spec: dict[float | None, int]) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for pct, n in spec.items():
        out.extend({"pct_chg": pct} for _ in range(n))
    return out


def test_bucket_label_sets_cover_market_distribution_exactly() -> None:
    """涨+跌标签集必须**恰好**等于市场分布的 11 个桶（多一个少一个都当场报错）。"""
    assert set(svc.DISTRIBUTION_UP_LABELS) | set(svc.DISTRIBUTION_DOWN_LABELS) == set(
        _DISTRIBUTION_RANGES
    )
    svc._assert_bucket_labels()  # 不抛


def test_bucket_label_drift_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    """市场侧改了桶标签而 S1.8 没同步 → RuntimeError（不许静默错数）。"""
    monkeypatch.setattr(svc, "DISTRIBUTION_RANGES", (*_DISTRIBUTION_RANGES, "新增桶"))
    with pytest.raises(RuntimeError, match="labels changed"):
        svc._assert_bucket_labels()


def test_breadth_counts_match_distribution_buckets_one_for_one() -> None:
    """**同源证据（纯函数层）**：本模块的家数 == 市场分布桶的逐桶和。

    样本刻意覆盖每个桶的边界值：-10 / -8 / -6 / -4 / -2 / -0.5 / **0** / 0.5 /
    2 / 4 / 10，以及 `None`（市场分布口径把 NULL 归 `0~1%`，与 `summarize_group`
    把 NULL 归平盘是两条不同的切法 —— 前者按桶、后者按符号，差额由
    `flat_in_positive_bucket` 显式记账）。
    """
    rows = _rows(
        {
            -10.0: 2,
            -8.0: 3,
            -6.0: 5,
            -4.0: 7,
            -2.0: 11,
            -0.5: 13,
            0.0: 17,
            0.5: 19,
            2.0: 23,
            4.0: 29,
            10.0: 31,
            None: 37,
        }
    )
    counts = svc.breadth_counts_from_rows(rows)

    # 逐桶相加 == 本模块的家数（两次独立求和，不是同一个值回读）
    buckets = svc.distribution_counts(rows)
    assert counts["up_count"] == sum(buckets[label] for label in svc.DISTRIBUTION_UP_LABELS)
    assert counts["down_count"] == sum(buckets[label] for label in svc.DISTRIBUTION_DOWN_LABELS)
    assert counts["total"] == len(rows)
    assert counts["up_count"] + counts["down_count"] + counts["flat_count"] == len(rows)

    # 负数侧：桶的边界与 `pct_chg < 0` **完全重合**（实测与构造双证）
    assert counts["down_count"] == counts["down_sign_count"] == 5 + 7 + 11 + 13 + 3 + 2
    # 平盘（0.0）与 NULL 都被市场分布的 `0~1%` 桶吸收 → 正数桶之和 = 符号上涨 + 平盘
    assert buckets["0~1%"] == 17 + 19 + 37
    assert counts["up_count"] == counts["up_sign_count"] + counts[svc.FLAT_IN_POSITIVE_BUCKET]
    assert counts[svc.FLAT_IN_POSITIVE_BUCKET] == 17 + 37


@pytest.mark.parametrize(
    ("pct", "label"),
    [
        (-10.0, "跌停"),
        (-9.5, "跌停"),
        (-8.0, ">-7%"),
        (-7.0, "-5~-7%"),  # 边界：`< -7` 为假 → 落 -5~-7%（标签 ">-7%" 是遗留误名，见市场模块注释）
        (-0.01, "0~-1%"),
        (0.0, "0~1%"),
        (0.99, "0~1%"),
        (1.0, "1~3%"),
        (9.5, "涨停"),
        (10.0, "涨停"),
        (None, "0~1%"),
    ],
)
def test_distribution_counts_uses_market_bucket_function(pct: float | None, label: str) -> None:
    """本模块的分桶就是市场模块的 `_distribution_bucket`（逐边界对拍，无第二套实现）。"""
    assert svc.distribution_counts(_rows({pct: 1}))[label] == 1
    assert _distribution_bucket(pct) == label


def test_advance_decline_ratio_is_bucket_based() -> None:
    """`advance_decline_ratio` = 上涨家数 / 下跌家数（桶口径），并写进 `extra`。"""
    rows = _rows({-1.0: 3, 0.0: 1, 1.0: 9})
    out = {m.metric_key: m for m in svc._breadth_metrics(svc.breadth_counts_from_rows(rows))}
    # up = 0~1%(0.0 一行) + 1~3%(9 行) = 10；down = 0~-1%(3 行) = 3
    assert out["market_advance_count"].value == 10.0
    assert out["market_decline_count"].value == 3.0
    assert out["advance_decline_ratio"].value == pytest.approx(10 / 3)
    assert out["advance_decline_ratio"].extra["sign_basis"] == "market_distribution_buckets"
    assert out["advance_decline_ratio"].extra["up_sign_count"] == 9
    assert out["advance_decline_ratio"].extra[svc.FLAT_IN_POSITIVE_BUCKET] == 1


def test_row_shape_and_null_extra_keeps_missing_reason() -> None:
    """NULL 值的行也必须落 `extra.missing_reason` —— 那是"为什么没值"的唯一证据。"""
    row = svc._row(
        date(2026, 1, 5),
        svc.MetricValue("limit_up_break_ratio", None, svc.MISSING_NO_SENTIMENT_DAY),
    )
    assert row["industry_key"] == "market" and row["stock_id"] == 0
    assert row["value"] is None
    assert row["source"] == MARKET_SENTIMENT_SOURCE and row["freq"] == "daily"
    assert row["published_at"] == svc.published_at_for(date(2026, 1, 5))
    assert row["caliber_version"] == MARKET_CALIBER_VERSION
    assert row["extra"] == {"missing_reason": svc.MISSING_NO_SENTIMENT_DAY}


def test_row_shape_value_has_no_missing_reason() -> None:
    row = svc._row(date(2026, 10, 9), svc.MetricValue("max_consecutive_limit_up", 9.0, None, {}))
    assert row["value"] == 9.0
    assert row["extra"] is None  # 有值的行不该带 missing_reason
    assert row["unit"] == "板"


def test_day_args_passes_date_objects() -> None:
    """raw SQL 走 `CAST(:day_lo AS date)`，实参必须还是 `date`（不是 isoformat 字符串）。"""
    args = svc._day_args(date(2026, 10, 1), date(2026, 10, 9))
    assert args == {"day_lo": date(2026, 10, 1), "day_hi": date(2026, 10, 9)}


def test_backfill_stats_as_dict_is_json_safe() -> None:
    stats = svc.BackfillStats(
        day_lo=date(2026, 10, 8),
        day_hi=date(2026, 10, 9),
        quote_days=2,
        rows_upserted=16,
        days_written=2,
        no_quote_row_days=[],
        no_ma20_window_days=[date(2026, 10, 8)],
        per_metric_null={"above_ma20_ratio": 1},
    )
    assert stats.as_dict() == {
        "day_lo": "2026-10-08",
        "day_hi": "2026-10-09",
        "quote_days": 2,
        "rows_upserted": 16,
        "days_written": 2,
        "no_quote_row_days": [],
        "no_ma20_window_days": ["2026-10-08"],
        "per_metric_null": {"above_ma20_ratio": 1},
    }


def test_metric_coverage_as_dict_is_json_safe() -> None:
    cov = svc.MetricCoverage(
        metric_key="above_ma20_ratio",
        period_from=None,
        period_to=None,
        rows=0,
        covered_days=0,
        null_days=0,
        missing_days=[],
        missing_reasons={},
    )
    assert cov.as_dict()["period_from"] is None
