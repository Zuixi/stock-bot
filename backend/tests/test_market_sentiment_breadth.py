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


def test_registry_descriptions_state_the_actual_breadth_basis() -> None:
    """**I4**：涨跌家数的 `description` 必须写出**实际**口径（桶之和）与缺失处理。

    旧描述说"`pct_chg > 0` 的家数""平盘（`pct_chg == 0` 或 NULL）既不计涨也不计跌"
    —— 两句都与落库值相反（主值是市场分布桶之和，含平盘；NULL 曾被记成上涨）。读面
    直接下发这个字符串，spec 又要求口径"查得到"，故用测试锁死关键措辞。
    """
    by_key = {m.key: m for m in MARKET_METRICS}
    up = by_key["market_advance_count"].description
    down = by_key["market_decline_count"].description
    adr = by_key["advance_decline_ratio"].description
    for desc in (up, down, adr):
        assert "partial_pct_chg" in desc, desc
        assert "unavailable_pct_chg" in desc, desc
    # 上涨家数：桶之和 + 含平盘（不是 pct_chg > 0）
    assert "上涨桶" in up and "0~1%" in up
    assert "pct_chg == 0" in up
    assert "flat_in_positive_bucket" in up
    # 下跌家数：NULL 不计涨跌（旧描述把 NULL 说成平盘）
    assert "pct_chg IS NULL" in down and "既不计涨也不计跌" in down
    assert "pct_chg IS NULL" in up
    # 涨跌比：分母 0 → NULL，覆盖率不足 → NULL
    assert "分母为 0" in adr and "partial_pct_chg" in adr


def test_registry_missing_reason_registry_includes_partial_pct_chg() -> None:
    """新原因码 `partial_pct_chg` 必须在 registry 的缺数语义表里可查（不是裸字符串）。"""
    from app.services.industry_registry import MARKET_METRIC_MISSING_REASONS

    assert MARKET_METRIC_MISSING_REASONS[svc.MISSING_PARTIAL_PCT_CHG]
    assert "覆盖率" in MARKET_METRIC_MISSING_REASONS[svc.MISSING_PARTIAL_PCT_CHG]


def test_find_metric_resolves_market_keys() -> None:
    """通用读面靠 `find_metric` 做白名单：8 个 key 必须都能解析到 'market' scope。"""
    from app.services.industry_registry import find_metric

    for key in svc.ALL_METRIC_KEYS:
        found = find_metric(key)
        assert found is not None, key
        assert found[0] == "market", key


def test_industry_predicate_excludes_market_scope() -> None:
    """**I3**：`is_industry` 是"行业数"的唯一判据，全市场 scope 必须被判为非行业。

    这条钉住徽标「行业分类（申万）」的分母与 `GET /api/v1/industries` 的过滤前提：
    `INDUSTRIES` 的 key 数 = scope 数（3），`get_industry_scopes()` = 行业数（2）。
    """
    from app.services.industry_registry import get_industry_scopes

    assert set(INDUSTRIES) == {"pig", "broiler", "market"}
    scopes = get_industry_scopes()
    assert [c.key for c in scopes] == ["pig", "broiler"]
    assert len(INDUSTRIES) == 3 and len(scopes) == 2
    assert INDUSTRIES["market"].is_industry is False
    assert INDUSTRIES["market"].sw_l3_codes == []
    for cfg in scopes:
        assert cfg.is_industry is True
        assert cfg.sw_l3_codes, cfg.key


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
    # 空行集（0 行）走同一条缺数路径，不得落成"覆盖率不足"（0/0 不该被读成 partial）
    empty = {m.metric_key: m for m in svc._breadth_metrics(svc.breadth_counts_from_rows([]))}
    for m in empty.values():
        assert m.value is None and m.missing_reason == svc.MISSING_NO_QUOTE_ROWS


def test_ma20_metric_missing_window_is_none_not_zero() -> None:
    """该日不在 MA20 结果里 → `None` + `no_ma20_window`（**不是 0%**）。"""
    m = svc._ma20_metric(date(2026, 1, 5), {})
    assert m.value is None
    assert m.missing_reason == svc.MISSING_NO_MA20_WINDOW
    assert m.extra["window"] == svc.MA20_WINDOW


def test_ma20_metric_ratio_and_extra() -> None:
    """`above / eligible × 100`，分子分母与窗口跨度都进 `extra`（可手算复核）。"""
    m = svc._ma20_metric(
        date(2026, 10, 9),
        {
            date(2026, 10, 9): svc.Ma20Day(
                5542, 2146, max_window_span_days=64, max_gap_days=33, span_over_alert_days=15
            )
        },
    )
    assert m.value == pytest.approx(2146 / 5542 * 100)
    assert m.extra == {
        "window": 20,
        "eligible": 5542,
        "above": 2146,
        "max_window_span_days": 64,
        "max_gap_days": 33,
        "span_over_alert_days": 15,
        "span_alert_days": svc.MA20_SPAN_ALERT_DAYS,
    }
    assert m.missing_reason is None


def test_ma20_metric_zero_eligible_is_none() -> None:
    """eligible = 0 → `None`（防御性：GROUP BY 不会产出 0 行的组，但分母为 0 不许落 0）。"""
    m = svc._ma20_metric(
        date(2026, 10, 9),
        {
            date(2026, 10, 9): svc.Ma20Day(
                0, 0, max_window_span_days=0, max_gap_days=0, span_over_alert_days=0
            )
        },
    )
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
    """**同源证据（纯函数层）**：可用行的家数 == 市场分布桶的逐桶和。

    样本刻意覆盖每个桶的边界值：-10 / -8 / -6 / -4 / -2 / -0.5 / **0** / 0.5 /
    2 / 4 / 10，以及 `None`（**Ruling 55 后 NULL 在分桶前剔除**）。
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
    usable = [r for r in rows if r["pct_chg"] is not None]
    buckets = svc.distribution_counts(usable)

    # 逐桶相加 == 本模块的家数（两次独立求和，不是同一个值回读）
    assert counts["up_count"] == sum(buckets[label] for label in svc.DISTRIBUTION_UP_LABELS)
    assert counts["down_count"] == sum(buckets[label] for label in svc.DISTRIBUTION_DOWN_LABELS)
    # NULL 行**不进** total（total = 可用行），单独记在 unavailable_pct_chg
    assert counts["total"] == len(usable) == len(rows) - 37
    assert counts["total_rows"] == len(rows)
    assert counts["unavailable_pct_chg"] == 37
    assert counts["coverage"] == pytest.approx(len(usable) / len(rows))
    assert counts["up_count"] + counts["down_count"] == counts["total"]

    # 负数侧：桶的边界与 `pct_chg < 0` **完全重合**（实测与构造双证）
    assert counts["down_count"] == counts["down_sign_count"] == 5 + 7 + 11 + 13 + 3 + 2
    # 平盘（0.0）留在市场分布的 `0~1%` 桶里；NULL **已被剔除**，不再被它吸收
    assert buckets["0~1%"] == 17 + 19
    assert counts["up_count"] == counts["up_sign_count"] + counts[svc.FLAT_IN_POSITIVE_BUCKET]
    assert counts[svc.FLAT_IN_POSITIVE_BUCKET] == 17
    # 旧 `flat_count = total - up - down` 是结构性死字段，已移除（不是改名）
    assert "flat_count" not in counts


def test_mostly_null_day_is_none_with_partial_reason_not_5382() -> None:
    """**C1 核心回归（Ruling 55）**：2025-09-23 那类"行情行在、`pct_chg` 几乎全 NULL"的日子。

    实测库内 2025-09-23：total_rows=5414 / 可用 32（全跌）/ NULL 5382。旧写法把 5382
    个 NULL 记成"上涨 5382"、`adr=168.19`；现在必须是：可用行里 up=0、down=32，覆盖率
    0.0059 < 门槛 → **三个指标全 `value=None` + `partial_pct_chg`**，诊断数字留在 `extra`。
    """
    rows = _rows({-1.0: 32, None: 5382})
    counts = svc.breadth_counts_from_rows(rows)
    assert counts["up_count"] == 0
    assert counts["down_count"] == 32
    assert counts["total_rows"] == 5414 and counts["total"] == 32
    assert counts["unavailable_pct_chg"] == 5382
    assert counts["coverage"] < svc.MIN_PCT_CHG_COVERAGE

    out = {m.metric_key: m for m in svc._breadth_metrics(counts)}
    for key in ("market_advance_count", "market_decline_count", "advance_decline_ratio"):
        assert out[key].value is None, f"{key} 不得由缺失数据拼出数字"
        assert out[key].missing_reason == svc.MISSING_PARTIAL_PCT_CHG
        assert out[key].extra["unavailable_pct_chg"] == 5382
        assert out[key].extra["coverage"] == pytest.approx(32 / 5414, abs=1e-6)
        assert out[key].extra["min_coverage"] == svc.MIN_PCT_CHG_COVERAGE
        assert "flat_count" not in out[key].extra


@pytest.mark.parametrize(
    ("usable", "unavailable", "expected_value"),
    [
        (950, 50, 950.0),  # 覆盖率恰为门槛 0.95 → 落值（>= 才通过）
        (949, 51, None),  # 略低于门槛 → NULL
    ],
)
def test_coverage_threshold_boundary(
    usable: int, unavailable: int, expected_value: float | None
) -> None:
    """覆盖率门槛是闭区间下界：`coverage >= MIN_PCT_CHG_COVERAGE` 才落值。"""
    rows = _rows({1.0: usable, None: unavailable})
    out = {m.metric_key: m for m in svc._breadth_metrics(svc.breadth_counts_from_rows(rows))}
    assert out["market_advance_count"].value == expected_value
    assert out["market_advance_count"].missing_reason == (
        None if expected_value is not None else svc.MISSING_PARTIAL_PCT_CHG
    )


def test_flat_identity_still_holds_after_null_exclusion() -> None:
    """**Ruling 54.1 的 117 平盘恒等式在剔除 NULL 后仍然成立**。

    用 2026-10-09 的真实库存（total=5559、正数桶之和 3414、符号上涨 3297、平盘 117、
    下跌 2145、无 NULL）构造同形样本：`up - up_sign == flat_in_positive_bucket == 117`，
    `adr == 3414/2145`，且 `flat_count` 不再出现在 `extra` 里。
    """
    rows = _rows({0.5: 3297, 0.0: 117, -0.5: 2145})
    counts = svc.breadth_counts_from_rows(rows)
    assert counts["unavailable_pct_chg"] == 0
    assert counts["coverage"] == 1.0
    assert counts["up_count"] == 3414
    assert counts["down_count"] == 2145
    assert counts["up_sign_count"] == 3297
    assert counts[svc.FLAT_IN_POSITIVE_BUCKET] == 117
    assert counts["up_count"] - counts["up_sign_count"] == counts[svc.FLAT_IN_POSITIVE_BUCKET]

    out = {m.metric_key: m for m in svc._breadth_metrics(counts)}
    assert out["market_advance_count"].value == 3414.0
    assert out["market_decline_count"].value == 2145.0
    assert out["advance_decline_ratio"].value == pytest.approx(3414 / 2145)
    assert out["advance_decline_ratio"].extra[svc.FLAT_IN_POSITIVE_BUCKET] == 117
    assert out["advance_decline_ratio"].extra["sign_basis"] == "market_distribution_buckets"
    assert "flat_count" not in out["advance_decline_ratio"].extra


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
    """本模块的分桶就是市场模块的 `_distribution_bucket`（逐边界对拍，无第二套实现）。

    含 `None → 0~1%`：这是**市场页的现状行为**（`distribution_counts` 的职责就是与
    市场端点逐桶对拍）。涨跌家数**不**直接用这个函数跑全量行 ——
    `breadth_counts_from_rows` 先剔除 NULL 再调它（Ruling 55）。
    """
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
    # 诊断字段：这天的分母构成可复核（C1 新增）
    assert out["advance_decline_ratio"].extra["total_rows"] == 13
    assert out["advance_decline_ratio"].extra["unavailable_pct_chg"] == 0
    assert out["advance_decline_ratio"].extra["coverage"] == 1.0
    assert "flat_count" not in out["advance_decline_ratio"].extra


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
