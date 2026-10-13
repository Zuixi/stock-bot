"""spec §S1.13 情绪 · **市场情绪综合指数** —— 纯函数与配置不变量（**不连库、不联网**）.

真库写入路径与**可复算性**在 `tests/test_market_sentiment_composite_db.py`。

这里锁的是"公开方法"本身（判据 2 的可复算性只有在方法**可逐字复述**时才可能成立）：

* 权重表/分位窗口/最小样本/归一化规则/版本号在 registry 里**自洽**（导入期断言 + 本文件复查）；
* 分位口径 = `indicators.percentile_rank`（nearest-rank、无插值、只用有效样本），
  反向腿先取相反数；
* **无前视**：某日的分位只用截至该日的窗口（截断序列与原序列在该日逐位相同）；
* 成分缺失 ⇒ **剔除 + 重新归一化**（有逐位手算的样例），绝不按 0 算、绝不插值；
* 参与不足 ⇒ `value=NULL` + `missing_reason`（不落 0）；
* `published_at` = 当天**全部**成分行的最大值（含当天不参与的成分）—— 不回退 `period`；
* 日历 = "至少一个成分有值"的日期的并集（历史尽量长，且逐日可查谁参与了）。
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import industry_registry as registry
from app.services import market_sentiment_composite_service as svc

CN = ZoneInfo("Asia/Shanghai")
DAY0 = date(2020, 1, 1)


def _days(n: int, start: date = DAY0) -> list[date]:
    """n 个连续日历日（纯函数不认交易日 —— 窗口单位是"观测行"）。"""
    return [start + timedelta(days=i) for i in range(n)]


def _obs(
    periods: list[date],
    values: list[float | None],
    *,
    at: tuple[int, int] = (15, 0),
) -> list[svc.ConstituentObservation]:
    stamp = time(hour=at[0], minute=at[1])
    return [
        svc.ConstituentObservation(
            period=p, value=v, published_at=datetime.combine(p, stamp, tzinfo=CN)
        )
        for p, v in zip(periods, values, strict=True)
    ]


def _spec(
    key: str, weight: float, direction: int = 1, group: str = "volatility_derivatives"
) -> registry.CompositeConstituent:
    return registry.CompositeConstituent(key, weight, direction, group, "test")


# ── A. 登记面不变量（权重表是公开契约）────────────────────────────────


def test_registry_weight_table_is_self_consistent() -> None:
    """权重之和 = 1、组权重与声明一致、方向可读、无重复 —— registry 导入期已断言，这里复查。"""
    total = sum(c.weight for c in registry.COMPOSITE_CONSTITUENTS)
    assert abs(total - 1.0) < 1e-9, total
    assert abs(sum(registry.COMPOSITE_GROUP_WEIGHTS.values()) - 1.0) < 1e-9
    for group, declared in registry.COMPOSITE_GROUP_WEIGHTS.items():
        actual = sum(c.weight for c in registry.COMPOSITE_CONSTITUENTS if c.group == group)
        assert abs(actual - declared) < 1e-9
    assert {c.direction for c in registry.COMPOSITE_CONSTITUENTS} <= {1, -1}
    assert len({c.metric_key for c in registry.COMPOSITE_CONSTITUENTS}) == len(
        registry.COMPOSITE_CONSTITUENTS
    )


def test_composite_is_registered_in_market_scope_with_pointer_to_method_doc() -> None:
    found = registry.find_metric(registry.COMPOSITE_METRIC_KEY)
    assert found is not None
    scope, metric = found
    assert scope == "market"
    assert metric.name == "市场情绪综合指数"
    assert metric.freq == "daily"
    assert metric.caliber_version == registry.COMPOSITE_CALIBER_VERSION == "s1.13-v1"
    assert "docs/design/metric-dictionary.md" in metric.description
    assert registry.MARKET_SENTIMENT.metric(registry.COMPOSITE_METRIC_KEY) is metric


def test_published_weight_table_matches_registry() -> None:
    """**公开权重表**（字典 §4.2 转录的就是这张）+ 分位口径必须与 registry 逐值一致.

    这是防"改了 registry 却没改公开文档"的机械闸门：两边任一漂移，本用例红。
    """
    published: dict[str, tuple[float, int]] = {
        "qvix_50etf": (0.12, -1),
        "if_basis": (0.09, 1),
        "ic_basis": (0.07, 1),
        "im_basis": (0.07, 1),
        "idx_turnover_ratio_csi300": (0.09, 1),
        "margin_financing_balance": (0.08, 1),
        "margin_financing_buy_ratio": (0.08, 1),
        "above_ma20_ratio": (0.12, 1),
        "advance_decline_ratio": (0.10, 1),
        "limit_up_break_ratio": (0.09, -1),
        "max_consecutive_limit_up": (0.09, 1),
    }
    assert {
        k: (c.weight, c.direction)
        for k, c in ((c.metric_key, c) for c in registry.COMPOSITE_CONSTITUENTS)
    } == published
    assert registry.COMPOSITE_PERCENTILE_WINDOW == 252
    assert registry.COMPOSITE_PERCENTILE_MIN_SAMPLES == 126
    assert registry.COMPOSITE_MIN_PARTICIPANTS == 2
    assert registry.COMPOSITE_MIN_EFFECTIVE_WEIGHT == 0.20
    assert registry.COMPOSITE_RENORMALIZATION == "drop_and_renormalise"
    assert registry.COMPOSITE_CALIBER_VERSION == "s1.13-v1"


def test_forbidden_metric_key_is_not_a_constituent() -> None:
    """被标注"不进信号链路"的 key 不得成为成分（机读挡板）。"""
    keys = {c.metric_key for c in registry.COMPOSITE_CONSTITUENTS}
    assert not keys & set(registry.COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS)
    assert "public_fund_equity_position" in registry.COMPOSITE_FORBIDDEN_CONSTITUENT_KEYS


def test_every_non_constituent_market_metric_has_a_written_reason() -> None:
    """§S1.13 的"为什么没有它"必须逐键可查（不许静默漏掉一条腿）。"""
    registered = {m.key for m in registry.MARKET_SENTIMENT.metrics}
    accounted = (
        {c.metric_key for c in registry.COMPOSITE_CONSTITUENTS}
        | set(registry.COMPOSITE_EXCLUDED_KEYS)
        | {registry.COMPOSITE_METRIC_KEY}
    )
    assert registered - accounted == set()
    assert set(registry.COMPOSITE_EXCLUDED_KEYS) - registered == set()


def test_composite_naming_has_no_borrowed_brand_name() -> None:
    """命名禁令（spec §S1.13）：只叫「市场情绪综合指数」，**不得**借用 CNN 那个美股口径的
    同名指数（名称本身是专有名词，与自建方法无关）。本用例把禁令机械化。"""
    banned = ("恐贪", "fear", "greed")
    metric = registry.MARKET_SENTIMENT.metric(registry.COMPOSITE_METRIC_KEY)
    assert metric is not None
    surface = f"{metric.name}{metric.description}".lower()
    assert [token for token in banned if token in surface] == []
    assert "综合指数" in metric.name


# ── B. 分位口径 ───────────────────────────────────────────────────────


def test_percentile_needs_min_samples() -> None:
    """有效样本 < 126 ⇒ **不产出分位**（不是 0 分位、不是 0.5）；达到后才产出。"""
    periods = _days(130)
    values = [float(i) for i in range(130)]
    short = svc.constituent_percentiles(_obs(periods[:125], values[:125]), direction=1)
    assert short == {}, "125 个有效样本 < 126 ⇒ 一个分位都不该有"
    ok = svc.constituent_percentiles(_obs(periods[:126], values[:126]), direction=1)
    assert ok[periods[125]] == pytest.approx(126 / 126)


def test_percentile_skips_none_samples_but_keeps_window_positions() -> None:
    """NULL 不进统计（不当 0）：130 行里有 5 行 NULL ⇒ 有效样本 125 ⇒ 仍不够 126。"""
    periods = _days(130)
    values: list[float | None] = [float(i) for i in range(130)]
    for idx in (0, 10, 20, 30, 40):
        values[idx] = None
    out = svc.constituent_percentiles(_obs(periods, values), direction=1)
    assert periods[129] not in out
    values[40] = 40.0
    out2 = svc.constituent_percentiles(_obs(periods, values), direction=1)
    assert periods[129] in out2


def test_direction_minus_one_negates_the_series_first() -> None:
    """反向腿（越高越恐慌）先取相反数：升序序列在 +1 下分位 ≈1，在 -1 下分位 = 1/n。"""
    periods = _days(130)
    values = [float(i) for i in range(130)]
    plus = svc.constituent_percentiles(_obs(periods, values), direction=1)
    minus = svc.constituent_percentiles(_obs(periods, values), direction=-1)
    assert plus[periods[-1]] == pytest.approx(1.0)
    assert minus[periods[-1]] == pytest.approx(1 / 130)


def test_percentile_has_no_lookahead() -> None:
    """某日的分位只看截至该日的窗口：截断到前 N 行后，那 N 行的分位逐位不变。"""
    periods = _days(400)
    values = [float((i * 7919) % 300) for i in range(400)]
    full = svc.constituent_percentiles(_obs(periods, values), direction=1)
    cut = svc.constituent_percentiles(_obs(periods[:300], values[:300]), direction=1)
    assert set(cut) <= set(full)
    assert {p: full[p] for p in cut} == cut


# ── C. 合成 + 缺失处置（逐位手算样例）────────────────────────────────


def test_missing_constituent_is_dropped_and_weights_renormalised() -> None:
    """**worked example**：三条腿（0.5 / 0.3 / 0.2），C 当日无行 ⇒ 剔除 + 归一化.

    手算（窗口 252、最小样本 126，两条腿都只有 130 行 ⇒ 窗口 = 全部行）：
    A 序列 1..130（升序）⇒ 末日值 130 是最大值 ⇒ p_A = 130/130 = 1
    B 序列 130..1（降序）⇒ 末日值 1 是最小值 ⇒ p_B = 1/130
    参与集 {A, B} ⇒ w'_A = 0.5/0.8 = 0.625、w'_B = 0.3/0.8 = 0.375
    value = 100 × (0.625 × 1 + 0.375 × 1/130) = 100 × 0.62788461538 = 62.7884615… → 62.7885
    """
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.3, group="activity_leverage")
    c = _spec("c", 0.2, group="breadth_momentum")
    observations = {
        "a": _obs(periods, [float(i + 1) for i in range(130)], at=(15, 0)),
        "b": _obs(periods, [float(130 - i) for i in range(130)], at=(16, 0)),
        "c": _obs(periods[:-1], [1.0] * 129, at=(18, 0)),  # 末日无行 ⇒ 不参与
    }
    days = svc.compose_series(observations, constituents=[a, b, c])
    last = days[-1]
    assert last.period == periods[-1]
    assert last.value == pytest.approx(62.7885, abs=1e-9)
    extra = last.extra
    assert extra["participants"] == ["a", "b"]
    assert extra["effective_weights"] == pytest.approx({"a": 0.625, "b": 0.375})
    assert extra["effective_weight_sum"] == 0.8
    assert extra["n_participants"] == 2
    assert extra["coverage"] == pytest.approx(2 / 3)
    assert extra["renormalization"] == "drop_and_renormalise"
    assert extra["constituents"]["c"] == {
        "value": None,
        "direction": 1,
        "percentile": None,
        "weight_configured": 0.2,
        "weight_effective": None,
        "participated": False,
        "reason": "no_row_today",
    }
    assert extra["constituents"]["a"]["percentile"] == 1.0
    # extra 里的分位以 double 全精度落库（若四舍五入到 6 位，重算值会被挪到 62.7884）
    assert extra["constituents"]["b"]["percentile"] == 1 / 130
    # 重算恒等式：value 就是 100 × Σ w'_i × p_i（可复算性的代数形式）
    recomputed = 100 * sum(
        extra["effective_weights"][k] * extra["constituents"][k]["percentile"]
        for k in extra["participants"]
    )
    assert recomputed == pytest.approx(62.78846153846154, abs=1e-9)
    assert round(recomputed, 4) == last.value
    assert last.missing_reason is None


def test_value_is_none_when_quorum_not_met() -> None:
    """只有 1 条腿参与 ⇒ NULL + `insufficient_participants`（**不落 0**）。"""
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)]),
        "b": _obs(periods[:10], [1.0] * 10),  # 样本永远不够 126
    }
    days = svc.compose_series(observations, constituents=[a, b])
    last = days[-1]
    assert last.value is None
    assert last.missing_reason == "insufficient_participants"
    assert last.extra["missing_reason"] == "insufficient_participants"
    assert last.extra["n_participants"] == 1
    assert last.extra["constituents"]["b"]["reason"] == "no_row_today"


def test_value_is_none_when_effective_weight_below_floor() -> None:
    """参与数够但生效权重之和 < 0.20 ⇒ NULL + `insufficient_effective_weight`。"""
    periods = _days(130)
    a = _spec("a", 0.06)
    b = _spec("b", 0.06)
    c = _spec("c", 0.88)
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)]),
        "b": _obs(periods, [float(i) for i in range(130)]),
        "c": _obs(periods[:5], [1.0] * 5),
    }
    days = svc.compose_series(observations, constituents=[a, b, c])
    assert days[-1].value is None
    assert days[-1].missing_reason == "insufficient_effective_weight"
    assert days[-1].extra["effective_weight_sum"] == pytest.approx(0.12)


def test_null_value_is_not_used_as_zero() -> None:
    """成分当日有行但 `value=NULL` ⇒ `value_missing`（该腿被剔除），**不当 0 参与**."""
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    b_values: list[float | None] = [float(i) for i in range(130)]
    b_values[-1] = None
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)]),
        "b": _obs(periods, b_values),
    }
    days = svc.compose_series(observations, constituents=[a, b])
    last = days[-1]
    assert last.extra["constituents"]["b"]["reason"] == "value_missing"
    assert last.extra["constituents"]["b"]["value"] is None
    assert last.missing_reason == "insufficient_participants"


def test_calendar_is_union_of_days_with_a_value() -> None:
    """日历 = "至少一个成分有值"的并集：**并集不是交集**（历史尽量长），且裁剪生效."""
    p_a = _days(10)
    p_b = _days(10, start=DAY0 + timedelta(days=5))
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    observations = {"a": _obs(p_a, [1.0] * 10), "b": _obs(p_b, [2.0] * 10)}
    days = svc.compose_series(observations, constituents=[a, b], min_participants=1)
    assert [d.period for d in days] == sorted(set(p_a) | set(p_b))
    clipped = svc.compose_series(
        observations, constituents=[a, b], min_participants=1, day_lo=p_b[0], day_hi=p_b[-1]
    )
    assert [d.period for d in clipped] == p_b


# ── D. PIT（合成值的可得性由成分决定）────────────────────────────────


def test_published_at_is_max_over_all_constituents_of_the_day() -> None:
    """`published_at` 取当天**全部**成分行的最大值 —— 含当天**不参与**的成分.

    为什么含不参与的：合成值定型的前提是"谁参与"也定了，而"某腿今天不可得"这件事
    同样要等它自己的可得时点才知道。用最大值 ⇒ **不可能早于当天最晚定型的成分**。
    """
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    c = _spec("c", 0.0)
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)], at=(15, 0)),
        "b": _obs(periods, [float(i) for i in range(130)], at=(16, 0)),
        # C 只到今天、且值为 NULL（不参与），但它的 published_at 是当天最晚的 19:00
        "c": _obs(periods[-1:], [None], at=(19, 0)),
    }
    days = svc.compose_series(observations, constituents=[a, b, c])
    last = days[-1]
    assert last.extra["constituents"]["c"]["participated"] is False
    assert last.published_at == datetime.combine(periods[-1], time(hour=19), tzinfo=CN)
    assert last.extra["latest_constituent"] == {
        "metric_key": "c",
        "published_at": last.published_at.isoformat(),
    }
    assert last.extra["pit_basis"] == "max_constituent_published_at"
    assert last.published_at.date() == last.period


def test_missing_constituent_published_at_fails_loudly() -> None:
    """成分行缺 `published_at` ⇒ **响亮失败**（绝不静默回退 `period`，Ruling 34/35）。"""
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    bad = [
        svc.ConstituentObservation(period=p, value=float(i), published_at=None)
        for i, p in enumerate(periods)
    ]
    observations = {"a": bad, "b": _obs(periods, [float(i) for i in range(130)])}
    with pytest.raises(RuntimeError, match="published_at"):
        svc.compose_series(observations, constituents=[a, b])


# ── E. 参与区间（"哪些成分在哪些日期生效"）──────────────────────────


def test_participant_eras_group_consecutive_identical_sets() -> None:
    """参与集**连续相同**的日子合成一个区间（"哪些成分在哪些日期区间生效"的机读形状）.

    b 只有 128 行 ⇒ 它的分位只在它自己的第 126~128 行（= periods[125..127]）有效；
    a 有 130 行 ⇒ 有效到 periods[129]。于是区间 = [{a,b} × 3 天, {a} × 2 天]
    （本用例把参与下限放宽到 1，好让"只剩 a"的两天也产生值 —— 否则它们是 NULL 日，
    按定义不进区间）。
    """
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)]),
        "b": _obs(periods[0:128], [float(i) for i in range(128)]),
    }
    days = svc.compose_series(
        observations, constituents=[a, b], min_participants=1, min_effective_weight=0.0
    )
    eras = svc.participant_eras(days)
    assert [e["participants"] for e in eras] == [["a", "b"], ["a"]]
    assert eras[0]["from"] == periods[125].isoformat()
    assert eras[0]["to"] == periods[127].isoformat()
    assert eras[0]["days"] == 3
    assert eras[0]["effective_weights"] == {"a": 0.5, "b": 0.5}
    assert eras[1]["from"] == periods[128].isoformat()
    assert eras[1]["to"] == periods[129].isoformat()
    assert eras[1]["effective_weights"] == {"a": 1.0}
    # NULL 日（这里是 periods[0..124]）不属于任何区间：它们没有生效权重
    assert sum(e["days"] for e in eras) == 5


def test_stats_summarise_rows_values_and_reasons() -> None:
    periods = _days(130)
    a = _spec("a", 0.5)
    b = _spec("b", 0.5)
    observations = {
        "a": _obs(periods, [float(i) for i in range(130)]),
        "b": _obs(periods[:10], [float(i) for i in range(10)]),
    }
    days = svc.compose_series(observations, constituents=[a, b])
    stats = svc.composite_stats(days, 0)
    assert stats.period_lo == periods[0] and stats.period_hi == periods[-1]
    assert stats.value_days == 0
    assert stats.null_days == 130
    assert stats.missing_reasons == {"insufficient_participants": 130}
    assert stats.participating_days == {"a": 5}
    assert stats.sources == {registry.COMPOSITE_METRIC_KEY: "local_calc"}
