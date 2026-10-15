"""因子库单测 —— **纯函数，无 DB、无 marker**（spec §S2.1 门禁：纯函数必须有单测）.

本文件**不** import 任何 DB fixture、不用 `client` / `db` fixture、不注册 marker；
`pytest tests/test_indicators.py -q` 在**没有数据库**的环境下也必须全绿
（证明见 `test_module_is_pure_no_io_imports`：本模块的 import 被 AST 机械盘点）。

三条锁定：
1. 口径（每个函数的边界与 None 语义）
2. 纯（不改入参、无 IO import、无副作用）
3. **可复算**（同一输入跑两次 bit-for-bit 相等）+ 旧定义重算历史不漂移
"""

from __future__ import annotations

import ast
import copy
import inspect
import math
from pathlib import Path

import pytest

from app.services import indicators as ind
from app.services.cycle_engine import count_consecutive_negative

# ── 样本（固定，不用随机数：可复算是本文件的主论题） ───────────────────


def _ramp(n: int, start: float = 1.0, step: float = 1.0) -> list[float]:
    return [start + i * step for i in range(n)]


class TestLatest:
    def test_last_valid_value(self):
        assert ind.latest([1.0, None, 3.0, None]) == 3.0

    def test_all_missing_is_none_not_zero(self):
        assert ind.latest([None, None]) is None

    def test_empty_series(self):
        assert ind.latest([]) is None

    def test_nan_is_treated_as_missing(self):
        assert ind.latest([1.0, math.nan, 2.0, math.nan]) == 2.0


class TestPctChange:
    def test_yoy_default_is_twelve_periods(self):
        series = [100.0] * 12 + [110.0]
        out = ind.yoy(series)
        assert out[11] is None  # 第 12 期才算得出
        assert out[12] == pytest.approx(0.10)

    def test_mom_default_is_one_period(self):
        out = ind.mom([100.0, 105.0, 105.0])
        assert out[0] is None
        assert out[1] == pytest.approx(0.05)
        assert out[2] == pytest.approx(0.0)

    def test_alignment_is_length_preserving(self):
        series = _ramp(20)
        assert len(ind.yoy(series)) == 20
        assert len(ind.mom(series)) == 20

    def test_zero_base_is_none_not_inf(self):
        out = ind.mom([0.0, 5.0])
        assert out[1] is None  # 基期 0 → 无定义；不得返回 inf

    def test_missing_either_side_is_none(self):
        assert ind.mom([None, 5.0])[1] is None
        assert ind.mom([5.0, None])[1] is None

    def test_non_positive_lag_rejected(self):
        with pytest.raises(ValueError):
            ind.mom([1.0], lag=0)


class TestPercentileRank:
    def test_full_window_ramp_ranks(self):
        out = ind.percentile_rank(_ramp(5), 5, min_samples=1)
        assert out == [1.0, 1.0, 1.0, 1.0, 1.0]  # 每个位置都是窗口内最大值

    def test_max_is_one_min_is_lowest(self):
        # 窗口 3、原序列 1..6：位置 i 的窗口是 [i-2, i]，当前值恒为窗口最大 → 1.0
        out = ind.percentile_rank(_ramp(6), 3)
        assert out[:2] == [None, None]  # 预热：有效样本 < window
        assert out[2:] == [1.0, 1.0, 1.0, 1.0]

    def test_rank_of_middle_value(self):
        # 窗口 [1,2,3]，当前值 2 → count(v<=2)=2 / 3
        series = [1.0, 2.0, 3.0, 2.0, 4.0]
        out = ind.percentile_rank(series, 3)
        assert out[3] == pytest.approx(2.0 / 3.0)

    def test_lowest_value_has_lowest_rank(self):
        series = [1.0, 2.0, 3.0, 0.0]
        out = ind.percentile_rank(series, 3)
        assert out[3] == pytest.approx(1.0 / 3.0)  # 窗口 [2,3,0] → 只有 0 自己 <= 0

    def test_requires_full_window_by_default(self):
        # min_samples 默认 = window（与 market_sentiment 的窗口完备守卫同义）
        out = ind.percentile_rank(_ramp(8), 5)
        assert out[:4] == [None, None, None, None]
        assert out[4] == pytest.approx(1.0)

    def test_min_samples_relaxes_warmup(self):
        out = ind.percentile_rank(_ramp(3), 5, min_samples=1)
        assert out == [1.0, 1.0, 1.0]  # 每个位置自己都是窗口内最大

    def test_ties_use_le_so_all_equal_is_one(self):
        assert ind.percentile_rank([7.0, 7.0, 7.0], 3) == [None, None, 1.0]
        assert ind.percentile_rank([7.0, 7.0, 7.0], 3, min_samples=1) == [1.0, 1.0, 1.0]

    def test_window_is_trailing(self):
        out = ind.percentile_rank([1.0, 2.0, 3.0, 100.0, 0.5], 2)
        assert out[-1] == pytest.approx(0.5)  # 窗口只含 [100.0, 0.5]

    def test_missing_values_do_not_count_as_zero(self):
        out = ind.percentile_rank([1.0, None, 3.0], 3, min_samples=2)
        assert out[2] == pytest.approx(1.0)  # 样本 = [1.0, 3.0]

    def test_no_samples_is_none(self):
        assert ind.percentile_rank([None, None], 2) == [None, None]

    def test_bad_window_rejected(self):
        with pytest.raises(ValueError):
            ind.percentile_rank([1.0], 0)


class TestZscore:
    def test_known_value(self):
        out = ind.zscore([1.0, 2.0, 3.0], 3)
        assert out[2] == pytest.approx(1.0 / math.sqrt(2.0 / 3.0))

    def test_flat_window_is_none_not_zero(self):
        assert ind.zscore([5.0, 5.0, 5.0], 3)[2] is None

    def test_warmup_is_none(self):
        assert ind.zscore(_ramp(5), 3)[:2] == [None, None]


class TestMovingAverage:
    def test_known_average(self):
        out = ind.moving_average(_ramp(5), 3)
        assert out[:2] == [None, None]
        assert out[2] == pytest.approx(2.0)
        assert out[4] == pytest.approx(4.0)

    def test_missing_blocks_by_default(self):
        out = ind.moving_average([1.0, None, 3.0], 3)
        assert out[2] is None

    def test_min_samples_allows_gapped_window(self):
        out = ind.moving_average([1.0, None, 3.0], 3, min_samples=2)
        assert out[2] == pytest.approx(2.0)

    def test_leading_warmup_uses_shorter_window_when_min_samples_set(self):
        out = ind.moving_average([1.0, 2.0], 5, min_samples=1)
        assert out == [1.0, 1.5]


class TestMaBullish:
    def test_true_when_short_above_long(self):
        out = ind.ma_bullish(_ramp(8), 2, 4)
        assert out[4] is True

    def test_false_when_short_below_long(self):
        out = ind.ma_bullish(list(reversed(_ramp(8))), 2, 4)
        assert out[4] is False
        assert out[7] is False

    def test_warmup_is_none(self):
        # short=2 → 第 2 个位置就有 MA2；long=4 → 第 4 个位置才有 MA4
        assert ind.ma_bullish(_ramp(8), 2, 4)[:3] == [None, None, None]
        assert ind.ma_bullish(_ramp(8), 2, 4)[3] is True  # MA2=3.5 > MA4=2.5

    def test_flat_series_is_not_bullish(self):
        assert ind.ma_bullish([3.0] * 6, 2, 4)[-1] is False  # 等于不算多头

    def test_short_must_be_shorter(self):
        with pytest.raises(ValueError):
            ind.ma_bullish(_ramp(6), 4, 4)


class TestTrendSlope:
    def test_perfect_ramp_gives_known_normalized_slope(self):
        out = ind.trend_slope([1.0, 2.0, 3.0], 3)
        assert out[2] == pytest.approx(0.5)  # slope=1, mean=2

    def test_raw_slope_option(self):
        out = ind.trend_slope([1.0, 2.0, 3.0], 3, normalize=False)
        assert out[2] == pytest.approx(1.0)

    def test_flat_series_slope_is_zero(self):
        assert ind.trend_slope([2.0] * 4, 4)[-1] == pytest.approx(0.0)

    def test_single_sample_is_none(self):
        assert ind.trend_slope([1.0], 3)[0] is None

    def test_zero_mean_cannot_normalize(self):
        assert ind.trend_slope([-1.0, 1.0], 2)[1] is None

    def test_slope_sign_is_negative_for_falling(self):
        out = ind.trend_slope([3.0, 2.0, 1.0], 3)
        assert out[2] < 0


class TestTrendReversals:
    def test_monotone_has_no_reversal(self):
        assert ind.trend_reversals(_ramp(6)) == 0

    def test_up_then_down_is_one_reversal(self):
        assert ind.trend_reversals([1.0, 2.0, 3.0, 2.0]) == 1

    def test_zigzag_counts_each_change(self):
        # 差分符号序列 [+,+,-,+,-] → 符号变化 3 次（相邻对 4 个里 3 个翻转）
        assert ind.trend_reversals([1.0, 2.0, 1.0, 2.0, 1.0]) == 3

    def test_flat_steps_do_not_manufacture_reversals(self):
        assert ind.trend_reversals([1.0, 1.0, 2.0, 2.0, 1.0]) == 1

    def test_missing_values_are_skipped(self):
        assert ind.trend_reversals([1.0, None, 2.0, None, 1.0]) == 1


class TestDrawdown:
    def test_monotone_rise_has_zero_drawdown(self):
        assert ind.drawdown(_ramp(5)) == [0.0, 0.0, 0.0, 0.0, 0.0]

    def test_peak_to_trough(self):
        out = ind.drawdown([10.0, 5.0, 8.0])
        assert out == [0.0, pytest.approx(-0.5), pytest.approx(-0.2)]

    def test_never_positive(self):
        out = [d for d in ind.drawdown([3.0, 1.0, 4.0, 2.0]) if d is not None]
        assert all(d <= 0 for d in out)

    def test_missing_values_do_not_reset_peak(self):
        out = ind.drawdown([10.0, None, 5.0])
        assert out[1] is None
        assert out[2] == pytest.approx(-0.5)

    def test_max_drawdown_is_min_of_series(self):
        assert ind.max_drawdown([10.0, 5.0, 8.0]) == pytest.approx(-0.5)

    def test_max_drawdown_of_window_is_trailing(self):
        # 全序列最大回撤 -0.5，但尾部 2 期只看 [8.0, 5.0] → -0.375
        assert ind.max_drawdown([10.0, 5.0, 8.0, 5.0], window=2) == pytest.approx(-0.375)

    def test_max_drawdown_empty_is_zero(self):
        assert ind.max_drawdown([]) == 0.0
        assert ind.max_drawdown([None, None]) == 0.0

    def test_max_drawdown_all_none_is_zero_not_none(self):
        assert ind.max_drawdown([None]) == 0.0


class TestVolatility:
    def test_flat_series_has_zero_volatility(self):
        out = ind.annualized_volatility([10.0] * 5, 5)
        assert out[4] == pytest.approx(0.0)

    def test_positive_for_moving_series(self):
        out = ind.annualized_volatility([1.0, 2.0, 1.0, 2.0, 1.0], 5)
        assert out[4] is not None
        assert out[4] > 0

    def test_annualisation_factor(self):
        # 交替 +100%/-50% 的样本，手工算一次
        series = [100.0, 200.0, 100.0]
        out = ind.annualized_volatility(series, 3)
        rets = [1.0, -0.5]
        mean = sum(rets) / 2
        expected = math.sqrt(sum((r - mean) ** 2 for r in rets) / 2) * math.sqrt(252)
        assert out[2] == pytest.approx(expected)

    def test_warmup_is_none(self):
        # 窗口 3 → 前 2 个位置缺收益率样本（不足 2 个）→ None
        assert ind.annualized_volatility(_ramp(5), 3)[:2] == [None, None]
        assert ind.annualized_volatility(_ramp(5), 3)[2] is not None

    def test_window_one_rejected(self):
        with pytest.raises(ValueError):
            ind.annualized_volatility([1.0, 2.0], 1)

    def test_downside_vol_only_uses_negative_returns(self):
        rising = ind.downside_volatility(_ramp(6), 3)
        assert rising[-1] is None  # 全是正收益 → None（不是 0.0）

    def test_downside_vol_positive_when_declines_exist(self):
        out = ind.downside_volatility([10.0, 9.0, 12.0, 6.0, 5.0], 5)
        assert out[-1] is not None
        assert out[-1] >= 0

    def test_zero_base_return_is_skipped_not_inf(self):
        out = ind.annualized_volatility([0.0, 1.0, 2.0, 3.0], 4)
        assert out[3] is not None
        assert not math.isinf(out[3])


class TestCountConsecutiveNegativeReuse:
    def test_is_the_cycle_engine_function_itself(self):
        assert ind.count_consecutive_negative is count_consecutive_negative

    def test_trailing_count(self):
        assert ind.count_consecutive_negative([1.0, -1.0, -2.0]) == 2

    def test_stops_at_non_negative(self):
        assert ind.count_consecutive_negative([-1.0, 0.0, -1.0]) == 1

    def test_empty(self):
        assert ind.count_consecutive_negative([]) == 0

    def test_none_breaks_run(self):
        assert ind.count_consecutive_negative([-1.0, None, -1.0]) == 1


class TestStockFeatureRow:
    def test_column_names_match_stock_features_model(self):
        from app.models.feature import StockFeature

        row = ind.stock_feature_row(_ramp(10), [100.0] * 10, window=5)
        columns = {c.key for c in StockFeature.__table__.columns}
        unknown = set(row) - columns
        assert unknown == set(), f"stock_feature_row 产出了模型里没有的列: {unknown}"

    def test_no_stock_features_column_is_left_unfilled(self):
        from app.models.feature import StockFeature

        row = ind.stock_feature_row(_ramp(10), [100.0] * 10, window=5)
        # 只要求"我们算的那批列"存在；分区/自增列由调用方给
        expected = {
            "total_return",
            "return_percentile",
            "annual_volatility",
            "max_drawdown",
            "downside_vol",
            "trend_slope",
            "ma_bullish",
            "trend_reversals",
            "avg_volume",
            "volume_volatility",
            "extra",
        }
        assert expected <= set(row)
        assert expected <= {c.key for c in StockFeature.__table__.columns}

    def test_extra_holds_long_window_factors_without_migration(self):
        long_window = ind.percentile_rank(_ramp(1260), 1260)
        row = ind.stock_feature_row(
            _ramp(10), [1.0] * 10, window=5, include_extra=[("sentiment_pct_1260", long_window)]
        )
        assert row["extra"] == {"sentiment_pct_1260": pytest.approx(1.0)}

    def test_extra_is_none_when_empty(self):
        row = ind.stock_feature_row(_ramp(3), [1.0] * 3, window=2)
        assert row["extra"] is None

    def test_deterministic(self):
        a = ind.stock_feature_row(_ramp(20), [1.0] * 20, window=10)
        b = ind.stock_feature_row(_ramp(20), [1.0] * 20, window=10)
        assert a == b

    def test_every_column_is_scoped_to_the_same_window(self):
        """同一行的所有列都落在同一个 window 上（含无窗口参数的因子）。

        构造：前 10 期是锯齿（很多反转）+ 深回撤，后 5 期单调上升。
        window=5 时，max_drawdown / trend_reversals **只看后 5 期** →
        不得把前 10 期的回撤与反转数带进来。
        """
        noisy = [10.0, 2.0, 9.0, 3.0, 8.0, 4.0, 7.0, 5.0, 6.0, 1.0]
        calm = [1.0, 2.0, 3.0, 4.0, 5.0]
        series = noisy + calm
        row = ind.stock_feature_row(series, [1.0] * len(series), window=5)
        assert row["max_drawdown"] == pytest.approx(0.0)  # 后 5 期单调上升
        assert row["trend_reversals"] == 0  # 后 5 期无反转
        # 对照：全序列口径会给出非零值（证明这条测试有区分力）
        assert ind.max_drawdown(series) < 0
        assert ind.trend_reversals(series) > 0

    def test_window_boundary_is_trailing(self):
        # 全序列峰值 5 → 尾部 1 = -80%；但 window=3 只看 [3,2,1] → 峰值 3 → -66.7%
        series = [1.0, 5.0, 4.0, 3.0, 2.0, 1.0]
        assert ind.stock_feature_row(series, [1.0] * 6, window=3)["max_drawdown"] == pytest.approx(
            -2.0 / 3.0
        )
        assert ind.stock_feature_row(series, [1.0] * 6, window=6)["max_drawdown"] == pytest.approx(
            -0.8
        )


# ── 纯函数性（无 IO / 无副作用） ──────────────────────────────────────

_PURE_FUNCS = [
    ind.latest,
    ind.latest_bool,
    ind.yoy,
    ind.mom,
    ind.percentile_rank,
    ind.zscore,
    ind.moving_average,
    ind.ma_bullish,
    ind.trend_slope,
    ind.trend_reversals,
    ind.drawdown,
    ind.max_drawdown,
    ind.annualized_volatility,
    ind.downside_volatility,
]


class TestPurity:
    def test_module_is_pure_no_io_imports(self):
        """AST 盘点 indicators.py 的 import：不得出现 IO/DB/网络/缓存依赖。

        这是"纯函数"的**机械**保证——比一句"我保证没 IO"可靠。允许的只有
        标准库纯计算模块与 `cycle_engine`（复用既有纯函数）。
        """
        src = Path(inspect.getfile(ind)).read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        forbidden = {
            "app.repositories",
            "app.core.database",
            "app.core.redis",
            "app.core.mq",
            "sqlalchemy",
            "redis",
            "asyncpg",
            "httpx",
            "aio_pika",
            "asyncio",
            "os",
            "socket",
            "subprocess",
            "pathlib",
            "logging",
            "requests",
            "time",
            "datetime",
        }
        hits = {m for m in imported if m in forbidden or m.startswith("app.repositories")}
        assert hits == set(), f"indicators.py 不得 import IO/副作用模块，命中: {sorted(hits)}"
        assert imported <= {"__future__", "math", "typing", "app.services.cycle_engine"}

    def test_module_has_no_dunder_io_side_effects(self):
        """模块级不得有 open/exec/eval/写文件（AST 层面）。"""
        src = Path(inspect.getfile(ind)).read_text(encoding="utf-8")
        tree = ast.parse(src)
        banned_calls = {"open", "exec", "eval", "compile", "__import__", "print"}
        found = [
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in banned_calls
        ]
        assert found == [], f"模块级/函数体里出现副作用调用: {found}"

    def test_functions_are_plain_defs_not_coroutines(self):
        for fn in [*_PURE_FUNCS, ind.stock_feature_row]:
            assert not inspect.iscoroutinefunction(fn), f"{fn.__name__} 不得是 async"

    def test_none_of_the_functions_accept_a_session_or_cache(self):
        """签名里不得出现 db/session/cache/conn 之类 IO 句柄参数。"""
        banned = ("db", "session", "cache", "conn", "client", "repo", "redis")
        for fn in [*_PURE_FUNCS, ind.stock_feature_row]:
            params = set(inspect.signature(fn).parameters)
            hits = {p for p in params if any(b in p.lower() for b in banned)}
            assert hits == set(), f"{fn.__name__} 的签名含 IO 句柄参数: {hits}"

    @pytest.mark.parametrize("fn", _PURE_FUNCS, ids=lambda f: f.__name__)
    def test_input_series_is_never_mutated(self, fn):
        series: list[float | None] = [3.0, None, 1.0, 4.0, 2.0, 5.0]
        snapshot = copy.deepcopy(series)
        params = inspect.signature(fn).parameters
        if "window" in params:
            fn(series, 3)
        elif "short_window" in params:
            fn(series, 2, 3)
        else:
            fn(series)
        assert series == snapshot, f"{fn.__name__} 改动了入参（违反纯函数）"

    @pytest.mark.parametrize("fn", _PURE_FUNCS, ids=lambda f: f.__name__)
    def test_same_input_same_output_twice_error_zero(self, fn):
        """spec 验收：同一输入跑两次，误差 = 0（可复算）。"""
        series = [3.0, None, 1.0, 4.0, 2.0, 5.0, 0.5]
        params = inspect.signature(fn).parameters
        if "window" in params:
            first, second = fn(series, 3), fn(series, 3)
        elif "short_window" in params:
            first, second = fn(series, 2, 3), fn(series, 2, 3)
        else:
            first, second = fn(series), fn(series)
        assert first == second

    def test_bool_series_helpers_are_pure(self):
        series: list[float | None] = [1.0, 2.0, None, 0.5]
        snapshot = copy.deepcopy(series)
        assert ind.ma_bullish(series, 1, 2) == ind.ma_bullish(series, 1, 2)
        assert series == snapshot


class TestRecomputability:
    """spec 验收：可复算 + "用旧定义重算历史仍得原值（不漂移）"。"""

    SERIES = [10.0, 12.0, 11.0, 15.0, 9.0, 9.5, 14.0, 13.0, 16.0, 12.0, 8.0, 8.5]

    def test_percentile_recompute_error_is_exactly_zero(self):
        a = ind.percentile_rank(self.SERIES, 6)
        b = ind.percentile_rank(self.SERIES, 6)
        assert a == b
        assert all(
            (x is None) == (y is None) and (x is None or abs(x - y) == 0.0)
            for x, y in zip(a, b, strict=True)
        )

    def test_max_drawdown_recompute_error_is_exactly_zero(self):
        a = ind.max_drawdown(self.SERIES, 6)
        b = ind.max_drawdown(self.SERIES, 6)
        assert a == b
        assert abs(a - b) == 0.0

    def test_old_definition_history_is_unchanged_by_new_definition(self):
        """旧定义重算历史 → 逐位等于当初物化的值（不漂移）。

        模拟"因子定义变更"：新定义换窗口（6 → 10），但**用旧定义（窗口 6）
        重算那条历史**必须复现出与当年完全相同的快照。若实现里藏了
        "当前窗口/当前时间"之类的隐式状态，这条必红。
        """
        frozen_old = ind.percentile_rank(self.SERIES, 6)  # 当年物化的值
        frozen_old_dd = ind.max_drawdown(self.SERIES, 6)
        _new_definition = ind.percentile_rank(self.SERIES, 10)

        recomputed_old = ind.percentile_rank(self.SERIES, 6)
        recomputed_old_dd = ind.max_drawdown(self.SERIES, 6)

        assert recomputed_old == frozen_old
        assert recomputed_old_dd == frozen_old_dd

    def test_history_length_does_not_change_past_values(self):
        """只看历史前 8 期算出的值 == 用全 12 期算出的前 8 期（PIT 不偷看未来）。"""
        short = ind.percentile_rank(self.SERIES[:8], 6)
        long = ind.percentile_rank(self.SERIES, 6)
        assert short == long[:8]

        short_dd = ind.drawdown(self.SERIES[:8])
        long_dd = ind.drawdown(self.SERIES)
        assert short_dd == long_dd[:8]

    def test_feature_row_is_stable_across_runs(self):
        vols = [100.0 * (i + 1) for i in range(len(self.SERIES))]
        first = ind.stock_feature_row(self.SERIES, vols, window=6)
        second = ind.stock_feature_row(self.SERIES, vols, window=6)
        assert first == second
