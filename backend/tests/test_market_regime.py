"""S10 市场状态机：五态表驱动 / 优先级 / 迟滞 / 转移矩阵 / 无前视 / 样本闸。

全部是**纯函数**用例（``compute_features`` / ``classify`` / ``classify_series`` /
``transition_matrix`` / ``forward_stats`` / ``read_regime``）：状态机不许查库，
重算只在离线脚本里，所以这里不需要 PG / redis / 起服务。

外加三条**产物契约**用例：入库产物与 registry 的版本+阈值对拍（含每态 ≥5% 的标定门禁）、
路由真的注册了、缺产物时读面给 ``state=null`` 而不是 500。

阈值一律从 ``svc.REGIME_THRESHOLDS`` 读、断言里**不写死数字**：spec §S10.2 要求阈值全部进
registry 且改阈值必须 bump 版本，用例若把 0.30/0.70 抄一遍，改标定就会变成"用例与实现
一起错"。
"""

from __future__ import annotations

import json
import statistics
from datetime import date, timedelta
from typing import Any

import pytest

from app.services import market_regime_service as svc

START = date(2026, 1, 5)


def _features(
    *,
    amount_pctile: float | None = 0.50,
    median_pct_chg: float | None = 0.50,
    new_high_ratio: float | None = 0.20,
    adv_dec_ratio: float | None = 1.0,
    amount_pctile_20d_ago: float | None = None,
    new_high_ratio_prev: float | None = None,
    new_high_ratio_5d_ago: float | None = None,
    realized_vol_20d: float | None = None,
    day: date = START,
) -> svc.DayFeatures:
    """造一天的特征（默认值本身是"中性"，用例只覆盖它关心的条件）。"""
    return svc.DayFeatures(
        trade_date=day,
        amount_pctile=amount_pctile,
        adv_dec_ratio=adv_dec_ratio,
        median_pct_chg=median_pct_chg,
        new_high_ratio=new_high_ratio,
        realized_vol_20d=realized_vol_20d,
        amount_pctile_20d_ago=amount_pctile_20d_ago,
        new_high_ratio_prev=new_high_ratio_prev,
        new_high_ratio_5d_ago=new_high_ratio_5d_ago,
    )


#: 五态表（spec §S10.2）。每行的**其它条件都明确不成立**，避免"通过"其实来自优先级：
#: 亢奋行新高不降、冰点行分位不到 0.30、退潮行同时满足修复（用来测优先级）、
#: 修复行 20 日回落很小、中性行两个条件都差一点。
_STATE_FIELDS: dict[str, dict[str, float | None]] = {
    "亢奋": dict(
        amount_pctile=0.90,
        median_pct_chg=1.2,
        new_high_ratio=0.40,
        amount_pctile_20d_ago=0.60,
        new_high_ratio_prev=0.35,
        new_high_ratio_5d_ago=0.20,
    ),
    "冰点": dict(
        amount_pctile=0.10,
        median_pct_chg=-1.5,
        new_high_ratio=0.05,
        amount_pctile_20d_ago=0.50,
        new_high_ratio_prev=0.20,
        new_high_ratio_5d_ago=0.02,
    ),
    "退潮": dict(
        amount_pctile=0.50,
        median_pct_chg=0.30,
        new_high_ratio=0.20,
        amount_pctile_20d_ago=0.80,
        new_high_ratio_prev=0.30,
        new_high_ratio_5d_ago=0.10,
    ),
    "修复": dict(
        amount_pctile=0.50,
        median_pct_chg=0.30,
        new_high_ratio=0.25,
        amount_pctile_20d_ago=0.60,
        new_high_ratio_prev=0.10,
        new_high_ratio_5d_ago=0.10,
    ),
    "中性": dict(
        amount_pctile=0.50,
        median_pct_chg=0.30,
        new_high_ratio=0.25,
        amount_pctile_20d_ago=0.52,
        new_high_ratio_prev=0.30,
        new_high_ratio_5d_ago=0.30,
    ),
}


def _days(count: int, start: date = START) -> list[date]:
    return [start + timedelta(days=index) for index in range(count)]


def _series(days: list[date], state: str = "修复") -> list[dict[str, Any]]:
    return [{"date": day, "state": state} for day in days]


def _run(states: list[str], start: date = START) -> list[svc.RegimePoint]:
    """把一串**规则态**喂给迟滞（每行取 ``_STATE_FIELDS`` 里那一态的完整特征）。"""
    features = [
        _features(day=start + timedelta(days=index), **_STATE_FIELDS[state])
        for index, state in enumerate(states)
    ]
    return svc.classify_series(features)


# ── 五态 + 优先级 + 边界 ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("expected", "fields"),
    list(_STATE_FIELDS.items()),
    ids=list(_STATE_FIELDS),
)
def test_classify_five_states(expected: str, fields: dict[str, float | None]) -> None:
    assert svc.classify(_features(**fields)) == expected


def test_priority_frenzy_beats_retreat() -> None:
    """亢奋 > 退潮：回落与新高下降都成立时仍是亢奋（spec §S10.2 的判定顺序）。"""
    features = _features(
        amount_pctile=0.85,
        median_pct_chg=1.0,
        new_high_ratio=0.40,
        amount_pctile_20d_ago=1.0,
        new_high_ratio_prev=0.45,
    )
    assert features.amount_pctile_20d_ago - features.amount_pctile > float(
        svc.REGIME_THRESHOLDS["retreat_amount_pctile_drop"]
    )
    assert features.new_high_ratio < features.new_high_ratio_prev  # type: ignore[operator]
    assert svc.classify(features) == "亢奋"


def test_priority_ice_beats_retreat() -> None:
    """冰点 > 退潮：低分位 + 中位下跌 + 新高萎缩同时也在"回落"，仍判冰点。"""
    features = _features(
        amount_pctile=0.10,
        median_pct_chg=-1.0,
        new_high_ratio=0.05,
        amount_pctile_20d_ago=0.60,
        new_high_ratio_prev=0.20,
    )
    assert features.amount_pctile_20d_ago - features.amount_pctile > float(
        svc.REGIME_THRESHOLDS["retreat_amount_pctile_drop"]
    )
    assert svc.classify(features) == "冰点"


def test_priority_retreat_beats_repair() -> None:
    """退潮 > 修复：分位在修复带内、中位上涨、新高 5 日也在升，但 20 日回落了 → 退潮。"""
    features = _features(
        amount_pctile=0.50,
        median_pct_chg=0.30,
        new_high_ratio=0.20,
        amount_pctile_20d_ago=0.80,
        new_high_ratio_prev=0.30,
        new_high_ratio_5d_ago=0.10,
    )
    low = float(svc.REGIME_THRESHOLDS["repair_amount_pctile_min"])
    high = float(svc.REGIME_THRESHOLDS["repair_amount_pctile_max"])
    assert low <= features.amount_pctile <= high  # type: ignore[operator]
    assert features.new_high_ratio > features.new_high_ratio_5d_ago  # type: ignore[operator]
    assert svc.classify(features) == "退潮"


def test_boundaries_are_strict_where_the_rule_says_strict() -> None:
    """边界值归哪一侧：全部按 registry 的当前数字断言（标定后数字变了用例不用改）。"""
    ice_edge = float(svc.REGIME_THRESHOLDS["ice_amount_pctile_below"])
    frenzy_edge = float(svc.REGIME_THRESHOLDS["frenzy_new_high_above"])
    repair_low = float(svc.REGIME_THRESHOLDS["repair_amount_pctile_min"])
    repair_high = float(svc.REGIME_THRESHOLDS["repair_amount_pctile_max"])

    # 冰点要 < 分位阈值：卡在阈值上 + 中位下跌 → 不是冰点（也不是修复，中位不涨）
    assert (
        svc.classify(
            _features(
                amount_pctile=ice_edge,
                median_pct_chg=-1.0,
                new_high_ratio=0.05,
                amount_pctile_20d_ago=ice_edge + 0.01,
                new_high_ratio_prev=0.05,
            )
        )
        == "中性"
    )
    # 亢奋要 > 新高阈值：卡在阈值上 + 高分位 → 不是亢奋（分位超修复上界 → 中性）
    assert (
        svc.classify(
            _features(
                amount_pctile=repair_high + 0.10,
                median_pct_chg=1.0,
                new_high_ratio=frenzy_edge,
                amount_pctile_20d_ago=repair_high,
                new_high_ratio_prev=frenzy_edge - 0.01,
                new_high_ratio_5d_ago=frenzy_edge - 0.02,
            )
        )
        == "中性"
    )
    # 修复带两端都是闭区间（spec 写的是 ≤）。新高压在亢奋阈值之下，否则高分段会被
    # 亢奋的优先级接走（带宽上界 0.80 已经高于亢奋的分位阈值 0.70）。
    for edge in (repair_low, repair_high):
        assert (
            svc.classify(
                _features(
                    amount_pctile=edge,
                    median_pct_chg=0.5,
                    new_high_ratio=frenzy_edge - 0.03,
                    amount_pctile_20d_ago=edge,
                    new_high_ratio_prev=frenzy_edge - 0.06,
                    new_high_ratio_5d_ago=frenzy_edge - 0.06,
                )
            )
            == "修复"
        )
    # 中位涨跌幅恰好 0：既不算"涨"也不算"跌" → 中性（不硬塞）
    assert (
        svc.classify(
            _features(
                amount_pctile=0.50,
                median_pct_chg=0.0,
                new_high_ratio=0.20,
                amount_pctile_20d_ago=0.55,
                new_high_ratio_prev=0.10,
                new_high_ratio_5d_ago=0.10,
            )
        )
        == "中性"
    )


@pytest.mark.parametrize(
    "missing",
    [
        {"amount_pctile": None},
        {"median_pct_chg": None},
        {"new_high_ratio": None},
    ],
    ids=["amount_pctile", "median_pct_chg", "new_high_ratio"],
)
def test_missing_required_feature_is_not_neutral(missing: dict[str, None]) -> None:
    """特征没就绪 = **不判**，不是"中性"（把缺数据画成中性会让人以为市场平淡）。"""
    assert svc.classify(_features(**missing)) is None


# ── 迟滞（3 日）─────────────────────────────────────────────────────────────


def test_hysteresis_blocks_a_one_day_jitter() -> None:
    """冰点→修复→冰点 各 1 天的抖动：状态**一次都不切换**（spec §S10.4）。"""
    points = _run(["冰点", "修复", "冰点", "修复", "冰点", "修复"])
    assert {point.state for point in points} == {"冰点"}
    assert {point.since for point in points} == {START}


def test_hysteresis_switches_on_the_third_consecutive_day() -> None:
    points = _run(["冰点", "冰点", "冰点", "修复", "修复", "修复", "修复"])
    assert [point.state for point in points] == ["冰点"] * 5 + ["修复"] * 2
    switched = points[5]
    # 切换日 = 连续满足的第 3 天（不是第 1 天）：否则 duration 会把确认期算进新状态
    assert switched.trade_date == START + timedelta(days=5)
    assert switched.since == switched.trade_date


def test_hysteresis_two_days_then_revert_does_not_switch() -> None:
    points = _run(["冰点", "修复", "修复", "冰点"])
    assert {point.state for point in points} == {"冰点"}


def test_hysteresis_streak_restarts_after_an_interruption() -> None:
    """修复、修复、冰点、修复、修复、修复 → 第 3 天被冰点打断，计数从第 4 天重新开始。"""
    points = _run(["冰点", "修复", "修复", "冰点", "修复", "修复", "修复"])
    assert [point.state for point in points] == ["冰点"] * 6 + ["修复"]
    assert points[-1].since == points[-1].trade_date == START + timedelta(days=6)


def test_hysteresis_days_comes_from_the_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """迟滞天数读 registry：改成 1 天就立刻切换（证明没有第二份写死的 3）。"""
    monkeypatch.setitem(svc.REGIME_THRESHOLDS, "hysteresis_days", 1)
    points = _run(["冰点", "修复", "修复"])
    assert [point.state for point in points] == ["冰点", "修复", "修复"]


# ── 转移矩阵 ────────────────────────────────────────────────────────────────


def test_transition_rows_sum_to_one() -> None:
    cycle = ["亢奋", "冰点", "退潮", "修复", "中性"]
    states = cycle * 2 + ["亢奋"]
    series = [{"date": day, "state": state} for day, state in zip(_days(len(states)), states)]
    matrix = svc.transition_matrix(series)
    assert set(matrix) == set(svc.STATES)  # 五态都真的作为起点出现过
    for state, row in matrix.items():
        assert set(row) == set(svc.STATES), f"{state} 的列必须齐全（缺的补 0）"
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-6), state
    # 每一行的转移就一条边：确定性检查（防止浮点求和"看起来"是 1）
    assert matrix["亢奋"]["冰点"] == pytest.approx(1.0)


def test_transition_matrix_omits_states_without_outgoing_edges() -> None:
    """只出现在末尾的态没有出边：不出行（0 行会让"行和=1"失效，均匀行是编造先验）。"""
    series = [
        {"date": START, "state": "冰点"},
        {"date": START + timedelta(days=1), "state": "修复"},
    ]
    matrix = svc.transition_matrix(series)
    assert "修复" not in matrix
    assert matrix["冰点"] == {"亢奋": 0.0, "冰点": 0.0, "退潮": 0.0, "修复": 1.0, "中性": 0.0}


# ── 同态后续分布：复合、winsorize、无前视、n<30 ──────────────────────────────


def test_forward_stats_compounds_over_trading_days() -> None:
    days = _days(60)
    returns = {day: 0.01 for day in days}
    row = svc.forward_stats(_series(days[:40]), returns)["修复"]
    assert row["n5"] == 40 and row["n20"] == 40
    assert row["t5_median"] == pytest.approx(round((1.01**5 - 1) * 100, 2))
    assert row["t20_median"] == pytest.approx(round((1.01**20 - 1) * 100, 2))


def test_forward_stats_refuses_numbers_below_min_samples() -> None:
    """spec §S10.4：每态 n<30 → 中位/最差 5% 一律 null，只给 n。"""
    days = _days(40)
    returns = {day: 0.01 for day in days}
    row = svc.forward_stats(_series(days[:10]), returns)["修复"]
    assert row["n5"] == 10 and row["n20"] == 10
    assert row["t5_median"] is None and row["t5_p05"] is None
    assert row["t20_median"] is None and row["t20_p05"] is None


def test_forward_stats_drops_incomplete_windows() -> None:
    """未来窗口不完整（末尾几天）→ 不计入该 horizon，绝不用 2 天冒充 20 天。"""
    days = _days(30)
    returns = {day: 0.01 for day in days}
    row = svc.forward_stats(_series(days), returns)["修复"]
    assert row["n5"] == 25
    assert row["n20"] == 10


def test_forward_stats_winsorizes_at_fifty_percent() -> None:
    days = _days(60)
    up = svc.forward_stats(_series(days[:40]), {day: 1.0 for day in days})["修复"]
    assert up["t5_median"] == 50.0 and up["t5_p05"] == 50.0
    down = svc.forward_stats(_series(days[:40]), {day: -0.9 for day in days})["修复"]
    assert down["t5_median"] == -50.0 and down["t5_p05"] == -50.0


def test_forward_stats_has_no_lookahead() -> None:
    """把**未来**收益塞进去，结果逐字段不变（窗口只看到每个入场日之后的固定 h 天）。"""
    days = _days(60)
    returns = {day: 0.01 for day in days}
    series = _series(days[:40])
    base = svc.forward_stats(series, returns)
    extended = {**returns, **{day: 0.5 for day in _days(200) if day not in returns}}
    assert svc.forward_stats(series, extended) == base


def test_forward_stats_never_uses_the_entry_day_return() -> None:
    """入场日当天收益不参与（收盘才判出的状态不能吃当天涨幅）：改它结果不变。"""
    days = _days(60)
    returns = {day: 0.01 for day in days}
    series = _series(days[:40])  # 首个入场日 = days[0]，它不在任何"入场日之后"的窗口里
    base = svc.forward_stats(series, returns)
    mutated = {**returns, days[0]: -0.9}
    assert svc.forward_stats(series, mutated) == base


# ── 特征（纯函数：不查库）────────────────────────────────────────────────────


def _raw(
    day: date,
    amount: float,
    *,
    advancers: int = 100,
    decliners: int = 50,
    median_pct_chg: float = 0.5,
    new_highs: int = 200,
    new_high_universe: int = 1000,
) -> svc.DailyRaw:
    return svc.DailyRaw(
        trade_date=day,
        total_amount=amount,
        advancers=advancers,
        decliners=decliners,
        median_pct_chg=median_pct_chg,
        new_highs=new_highs,
        new_high_universe=new_high_universe,
    )


def test_amount_pctile_warms_up_then_uses_a_250_day_window() -> None:
    min_days = int(svc.REGIME_THRESHOLDS["amount_pctile_min_days"])
    window = int(svc.REGIME_THRESHOLDS["amount_pctile_window"])
    # 两座"高峰"：第 0 天与第 100 天；到第 299 天时第 0 天已滑出窗口
    amounts = [1.0] * 300
    amounts[0] = 1e9
    amounts[100] = 1e9
    features = svc.compute_features(
        [_raw(START + timedelta(days=i), amounts[i]) for i in range(300)]
    )
    assert features[min_days - 2].amount_pctile is None  # 第 59 天还没到 60 天
    assert features[min_days - 1].amount_pctile is not None
    # 第 299 天的窗口 = 第 50..299 天（250 天），含第 100 天的高峰 → 249/250
    assert features[299].amount_pctile == pytest.approx((window - 1) / window)
    # 分位含当日：窗口内全是同一水平时，今天也算"不低于"（避免 0 分位）
    flat = svc.compute_features([_raw(START + timedelta(days=i), 5.0) for i in range(70)])
    assert flat[-1].amount_pctile == pytest.approx(1.0)


def test_adv_dec_and_new_high_ratio() -> None:
    rows = [
        _raw(START, 10.0, advancers=100, decliners=50, new_highs=20, new_high_universe=100),
        _raw(START + timedelta(days=1), 11.0, advancers=30, decliners=0),
        _raw(
            START + timedelta(days=2),
            12.0,
            new_highs=10,
            new_high_universe=99,
        ),
    ]
    features = svc.compute_features(rows)
    assert features[0].adv_dec_ratio == pytest.approx(2.0)
    assert features[0].new_high_ratio == pytest.approx(0.2)
    # 没有下跌家数 → 比例未定义（不是"无穷"）
    assert features[1].adv_dec_ratio is None
    # 分母不足 → 不给比例（小分母的 0.10 没有意义）
    assert features[2].new_high_ratio is None


def test_realized_vol_is_the_twenty_day_rolling_stdev_of_the_median() -> None:
    medians = [(-1) ** i * (0.1 * (i % 5)) for i in range(40)]
    rows = [_raw(START + timedelta(days=i), 10.0 + i, median_pct_chg=medians[i]) for i in range(40)]
    features = svc.compute_features(rows)
    window = int(svc.REGIME_THRESHOLDS["vol_window"])
    assert features[window - 2].realized_vol_20d is None
    assert features[window - 1].realized_vol_20d == pytest.approx(
        statistics.pstdev(medians[:window])
    )
    assert features[39].realized_vol_20d == pytest.approx(statistics.pstdev(medians[40 - window :]))


def test_lag_fields_match_the_registry_windows() -> None:
    rows = [_raw(START + timedelta(days=i), 10.0 + i) for i in range(60)]
    features = svc.compute_features(rows)
    assert features[4].new_high_ratio_5d_ago is None
    assert features[4].new_high_ratio_prev == features[3].new_high_ratio
    assert features[20].amount_pctile_20d_ago == features[0].amount_pctile
    assert features[19].amount_pctile_20d_ago is None


def test_compute_features_is_independent_of_input_order() -> None:
    rows = [_raw(START + timedelta(days=i), 10.0 + (i % 7)) for i in range(80)]
    assert svc.compute_features(rows) == svc.compute_features(list(reversed(rows)))


# ── 序列 / 产物装配 ─────────────────────────────────────────────────────────


def test_build_series_skips_the_warmup_prefix() -> None:
    min_days = int(svc.REGIME_THRESHOLDS["amount_pctile_min_days"])
    rows = [_raw(START + timedelta(days=i), 10.0 + i) for i in range(120)]
    series = svc.build_series(rows)
    # 成交额分位要满 min_days 天才出 → 序列从第 min_days 天开始，长度 = 120 - (min_days-1)
    assert series[0]["date"] == START + timedelta(days=min_days - 1)
    assert len(series) == 120 - (min_days - 1)
    assert series[-1]["date"] == START + timedelta(days=119)
    assert {point["state"] for point in series} <= set(svc.STATES)


def test_build_document_is_deterministic_and_self_describing() -> None:
    rows = [_raw(START + timedelta(days=i), 10.0 + i) for i in range(120)]
    returns = {row.trade_date: 0.001 for row in rows}
    first = svc.build_document(rows, returns)
    second = svc.build_document(list(reversed(rows)), dict(reversed(list(returns.items()))))
    assert svc.dump_document(first) == svc.dump_document(second)  # 两次运行逐字节一致
    assert first["version"] == svc.REGIME_VERSION
    assert first["thresholds"] == dict(svc.REGIME_THRESHOLDS)  # 产物自带阈值快照
    assert first["as_of"] == first["series"][-1]["date"]  # ISO 字符串
    assert json.loads(svc.dump_document(first)) == first


# ── 读面（产物 → 契约，降级不 500）──────────────────────────────────────────


def _write_artifact(
    path: Any,
    *,
    states: tuple[str, ...] = ("冰点", "修复", "修复"),
    version: str = svc.REGIME_VERSION,
) -> Any:
    days = _days(len(states))
    series = [{"date": day, "state": state} for day, state in zip(days, states)]
    document = {
        "version": version,
        "as_of": days[-1].isoformat(),
        "basis": svc.FORWARD_NOTE,
        "thresholds": dict(svc.REGIME_THRESHOLDS),
        "series": [{"date": day.isoformat(), "state": state} for day, state in zip(days, states)],
        "transitions": svc.transition_matrix(series),
        "forward": {
            "修复": {
                "n5": 5,
                "t5_median": None,
                "t5_p05": None,
                "n20": 4,
                "t20_median": None,
                "t20_p05": None,
            }
        },
    }
    path.write_text(svc.dump_document(document), encoding="utf-8")
    return path


def test_read_regime_projects_the_artifact(tmp_path: Any) -> None:
    path = _write_artifact(tmp_path / "regime.json")
    view = svc.read_regime(path, latest_trade_date=_days(3)[-1])
    assert view.state == "修复"
    assert view.duration_days == 2
    assert view.since == _days(3)[1]
    assert view.as_of == _days(3)[-1]
    assert sum(view.transition.values()) == pytest.approx(1.0)
    assert view.forward["5d"] == {"n": 5, "median_pct": None, "p05_pct": None}
    assert view.forward["20d"] == {"n": 4, "median_pct": None, "p05_pct": None}
    assert svc.SAMPLES_NOTE in view.notes
    assert any("n=5（<30）" in note for note in view.notes)
    assert any("n=4（<30）" in note for note in view.notes)


def test_read_regime_flags_version_drift_and_stale_data(tmp_path: Any) -> None:
    path = _write_artifact(tmp_path / "regime.json", version="s10.0")
    view = svc.read_regime(path, latest_trade_date=_days(3)[-1] + timedelta(days=3))
    assert any("不一致" in note for note in view.notes)
    assert any("最新交易日" in note for note in view.notes)


def test_read_regime_missing_artifact_is_a_null_state(tmp_path: Any) -> None:
    view = svc.read_regime(tmp_path / "missing.json", latest_trade_date=None)
    assert view.state is None
    assert view.since is None and view.duration_days == 0
    assert view.transition == {}
    assert view.forward == {"5d": None, "20d": None}
    assert view.notes[0] == svc.MISSING_NOTE


@pytest.mark.parametrize(
    "payload",
    ["{not json", json.dumps({"version": "x", "series": []}), json.dumps({"series": "nope"})],
    ids=["bad-json", "empty-series", "series-not-list"],
)
def test_read_regime_broken_artifact_is_a_null_state(tmp_path: Any, payload: str) -> None:
    path = tmp_path / "regime.json"
    path.write_text(payload, encoding="utf-8")
    view = svc.read_regime(path)
    assert view.state is None
    assert view.notes[0] == svc.BROKEN_NOTE


def test_committed_artifact_matches_the_registry() -> None:
    """入库产物必须与当前 registry 同版本同阈值；且五态各 ≥5% 天数（spec §S10.5）。"""
    assert svc.HISTORY_PATH.exists(), "产物未入库：运行 backend/scripts/build_market_regime.py"
    document = json.loads(svc.HISTORY_PATH.read_text(encoding="utf-8"))
    assert document["version"] == svc.REGIME_VERSION, "阈值改过但产物没复跑"
    assert document["thresholds"] == dict(svc.REGIME_THRESHOLDS), "产物阈值与 registry 漂移"
    series = document["series"]
    assert document["as_of"] == series[-1]["date"]
    assert {point["state"] for point in series} <= set(svc.STATES)
    for state, row in document["transitions"].items():
        assert sum(row.values()) == pytest.approx(1.0, abs=1e-6), state
    share = {
        state: sum(1 for point in series if point["state"] == state) / len(series)
        for state in svc.STATES
    }
    assert min(share.values()) >= 0.05, f"有态不足 5% 天数，需按 spec §S10.5 重新标定：{share}"


# ── 路由（注册 + 缺产物降级）────────────────────────────────────────────────


def test_regime_route_is_registered() -> None:
    from app.api.v1 import router as api_v1_router

    paths = {getattr(route, "path", "") for route in api_v1_router.routes}
    assert "/api/v1/market/regime" in paths


async def test_regime_route_degrades_without_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.api.v1 import market_regime as api
    from app.repositories import limit_up_repo

    monkeypatch.setattr(svc, "HISTORY_PATH", tmp_path / "missing.json")

    async def _latest(db: Any) -> date:
        return _days(3)[-1]

    monkeypatch.setattr(limit_up_repo, "latest_quote_date", _latest)
    out = await api.get_market_regime(db=object(), _user=object())
    assert out.state is None
    assert out.forward == {"5d": None, "20d": None}
    assert out.notes[0] == svc.MISSING_NOTE


async def test_regime_route_projects_forward_buckets(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Any
) -> None:
    from app.api.v1 import market_regime as api
    from app.repositories import limit_up_repo

    path = _write_artifact(tmp_path / "regime.json")
    monkeypatch.setattr(svc, "HISTORY_PATH", path)

    async def _latest(db: Any) -> date:
        return _days(3)[-1]

    monkeypatch.setattr(limit_up_repo, "latest_quote_date", _latest)
    out = await api.get_market_regime(db=object(), _user=object())
    assert out.state == "修复"
    assert out.duration_days == 2
    assert out.forward["5d"] is not None
    assert out.forward["5d"].n == 5
    assert out.forward["5d"].median_pct is None
    assert out.as_of == _days(3)[-1]


# ── registry 自身的一致性（不依赖外部文件）─────────────────────────────────


def test_registry_is_complete_and_sane() -> None:
    assert set(svc.REQUIRED_THRESHOLDS) == set(svc.REGIME_THRESHOLDS)
    assert int(svc.REGIME_THRESHOLDS["hysteresis_days"]) >= 1
    assert 0.0 < float(svc.REGIME_THRESHOLDS["winsorize"]) < 1.0
    assert int(svc.REGIME_THRESHOLDS["forward_horizon_short"]) < int(
        svc.REGIME_THRESHOLDS["forward_horizon_long"]
    )
    # 五态顺序 = 优先级（spec §S10.2），改顺序会改变"同时满足"的答案
    assert svc.STATES == ("亢奋", "冰点", "退潮", "修复", "中性")
