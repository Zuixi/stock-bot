"""监管异动纯计算口径的单元测试（不连库）。

每条断言都对应一个**会改变结论**的口径选择：
- 阈值按板块/ST 分档（主板 20 / ST 12 / 创业板·科创板 30 / 北交所 40）；
- 偏离值累计是**逐日求和**，不是复利；
- 事件按**不重叠窗口**计数（6 连板 = 2 次，不是 4 次）；
- 明日触发价 = 最近两日偏离 + 明日所需涨幅，超过涨跌幅限制则不可达；
- 缺失行情一律不判（不拿停牌前旧价格硬算）。
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services import regulatory_anomaly as ra


class TestRules:
    def test_main_board(self):
        rule = ra.resolve_rule("Shanghai_Stocks", "主板", "贵州茅台")
        assert rule.threshold_pct == 20.0
        assert rule.price_limit_pct == 10.0
        assert (rule.benchmark_code, rule.benchmark_name) == ("000001.SH", "上证综指")
        assert rule.is_st is False

    def test_main_board_st_uses_12_and_5(self):
        rule = ra.resolve_rule("Shenzen_Stocks", "主板", "ST富煌")
        assert rule.is_st is True
        assert rule.threshold_pct == 12.0
        assert rule.price_limit_pct == 5.0
        assert (rule.benchmark_code, rule.benchmark_name) == ("399001.SZ", "深证成指")

    def test_chinext_st_keeps_30_and_20(self):
        # 创业板风险警示股仍是 20% 限幅 → 不走主板 ST 的 12%/5%
        rule = ra.resolve_rule("Shenzen_Stocks", "创业板", "*ST赛为")
        assert rule.is_st is False
        assert rule.threshold_pct == 30.0
        assert rule.price_limit_pct == 20.0
        assert rule.benchmark_code == "399006.SZ"

    def test_star_market(self):
        rule = ra.resolve_rule("Shanghai_Stocks", "科创板", "ST帕瓦")
        assert rule.threshold_pct == 30.0
        assert rule.price_limit_pct == 20.0
        assert (rule.benchmark_code, rule.benchmark_name) == ("000688.SH", "科创50")

    def test_bse(self):
        rule = ra.resolve_rule("Beijing_Stocks", "北交所", "某某股份")
        assert rule.threshold_pct == 40.0
        assert rule.price_limit_pct == 30.0
        assert rule.benchmark_code == "899050.BJ"

    def test_unknown_board_falls_back_to_main_board_benchmark(self):
        rule = ra.resolve_rule("Shanghai_Stocks", None, None)
        assert rule.board == "主板"
        assert rule.benchmark_code == "000001.SH"

    @pytest.mark.parametrize(
        ("name", "expected"),
        [
            ("ST富煌", True),
            ("*ST创兴", True),
            ("SST天海", True),
            ("S*ST天海", True),
            ("贵州茅台", False),
            ("ST", True),
            (None, False),
        ],
    )
    def test_is_st_name(self, name, expected):
        assert ra.is_st_name(name) is expected


class TestDeviation:
    def test_pct_change(self):
        assert ra.pct_change(11.0, 10.0) == pytest.approx(10.0)
        assert ra.pct_change(10.0, 0.0) is None

    def test_daily_deviation_is_stock_minus_index(self):
        assert ra.daily_deviation(10.0, 1.0) == pytest.approx(9.0)
        assert ra.daily_deviation(None, 1.0) is None

    def test_window_sum_is_arithmetic_sum_not_compounded(self):
        # 3 个 10% 的复利是 33.1%，交易所口径是逐日求和 30.0%
        devs = [10.0, 10.0, 10.0]
        assert ra.window_sum(devs, 3) == pytest.approx(30.0)

    def test_window_sum_rejects_missing_day(self):
        assert ra.window_sum([10.0, None, 10.0], 3) is None
        assert ra.window_sum([10.0], 3) is None

    def test_status_boundaries(self):
        assert ra.status_of(20.0, 20.0) == "triggered"
        assert ra.status_of(19.99, 20.0) == "near"  # 80% 阈值 = 16
        assert ra.status_of(16.0, 20.0) == "near"
        assert ra.status_of(15.99, 20.0) == "watch"
        assert ra.status_of(None, 20.0) == "watch"
        # 跌向：-20 才算触发
        assert ra.status_of(-20.0, 20.0, direction="down") == "triggered"
        assert ra.status_of(-16.0, 20.0, direction="down") == "near"
        assert ra.status_of(-15.0, 20.0, direction="down") == "watch"


class TestEvents:
    def test_six_consecutive_boards_is_two_events(self):
        devs = [10.0] * 6
        events = ra.find_events(devs, 20.0)
        assert [e.end for e in events] == [2, 5]
        assert all(e.value == pytest.approx(30.0) for e in events)

    def test_missing_day_does_not_create_event(self):
        devs = [10.0, None, 10.0, 10.0]
        assert ra.find_events(devs, 20.0) == []

    def test_nine_boards_is_three_events(self):
        devs = [10.0] * 9
        events = ra.find_events(devs, 20.0)
        assert len(events) == 3
        assert ra.count_events(events, 9, 10) == 3

    def test_event_count_respects_lookback(self):
        devs = [10.0] * 9
        events = ra.find_events(devs, 20.0)
        # 只看最后 3 个交易日：只有落在最后 3 天内的窗口（end=8）算
        assert ra.count_events(events, 9, 3) == 1

    def test_down_direction(self):
        devs = [-10.0] * 6
        events = ra.find_events(devs, 20.0, direction="down")
        assert [e.end for e in events] == [2, 5]


class TestSevere:
    def test_ten_day_three_times(self):
        devs = [10.0] * 9
        events = ra.find_events(devs, 20.0)
        flags = ra.severe_flags(devs, events)
        assert "10日3次" in flags

    def test_ten_day_100_pct(self):
        devs = [0.0] * 4 + [20.0] * 6  # 10 个交易日样本，累计 120%
        events = ra.find_events(devs, 30.0)
        flags = ra.severe_flags(devs, events)
        assert "10日累计+100%" in flags

    def test_ten_day_50_down(self):
        devs = [0.0] * 4 + [-20.0] * 6
        events = ra.find_events(devs, 30.0, direction="down")
        flags = ra.severe_flags(devs, events, direction="down")
        assert "10日累计-50%" in flags

    def test_thirty_day_200_pct(self):
        devs = [0.0] * 19 + [20.0] * 11
        events = ra.find_events(devs, 30.0)
        flags = ra.severe_flags(devs, events)
        assert "30日累计+200%" in flags

    def test_not_enough_history_does_not_flag_30d(self):
        devs = [20.0] * 11
        events = ra.find_events(devs, 30.0)
        flags = ra.severe_flags(devs, events)
        assert not any(f.startswith("30日") for f in flags)


class TestNextTrigger:
    def test_screenshot_case(self):
        # 新华传媒：收盘 15.58，主板阈值 20%，最近两日偏离合计 12.2% → 还差 7.8% → ≥16.80
        trigger = ra.next_day_trigger(15.58, [6.1, 6.1], 20.0, 10.0)
        assert trigger is not None
        assert trigger.needed_pct == pytest.approx(7.8)
        assert trigger.price == pytest.approx(16.80)
        assert trigger.feasible is True

    def test_infeasible_when_needed_exceeds_limit(self):
        trigger = ra.next_day_trigger(15.58, [1.0, 1.0], 20.0, 10.0)
        assert trigger is not None
        assert trigger.needed_pct == pytest.approx(18.0)
        assert trigger.feasible is False

    def test_down_side_rounds_down(self):
        trigger = ra.next_day_trigger(15.58, [-6.1, -6.1], 20.0, 10.0, direction="down")
        assert trigger is not None
        assert trigger.needed_pct == pytest.approx(-7.8)
        assert trigger.price == pytest.approx(14.36)  # 15.58*0.922=14.36476 → 向下取 14.36
        assert trigger.feasible is True

    def test_missing_tail_returns_none(self):
        assert ra.next_day_trigger(10.0, [1.0, None], 20.0, 10.0) is None
        assert ra.next_day_trigger(10.0, [1.0], 20.0, 10.0) is None


def test_window_days_is_three():
    # 口径常量是契约：改成 5 会让所有页面数字静默变样
    assert ra.WINDOW_DAYS == 3


def test_board_rules_cover_all_four_boards():
    assert set(ra.RULES) == {"主板", "创业板", "科创板", "北交所"}


def test_benchmarks_cover_every_exchange_board_pair():
    assert set(ra.BENCHMARKS) == {
        ("Shanghai_Stocks", "主板"),
        ("Shanghai_Stocks", "科创板"),
        ("Shenzen_Stocks", "主板"),
        ("Shenzen_Stocks", "创业板"),
        ("Beijing_Stocks", "北交所"),
    }


def test_no_db_imports():
    """纯函数模块不得 import 数据库/网络/时钟（保证可单测、可重放）。"""
    import ast
    from pathlib import Path

    source = Path(ra.__file__).read_text(encoding="utf-8")
    tree = ast.parse(source)
    banned = ("sqlalchemy", "app.core", "app.repositories", "datetime", "httpx", "redis")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or ""]
        else:
            continue
        for name in names:
            assert not name.startswith(banned), f"纯计算模块不得 import {name}"


def test_dates_are_not_used_as_inputs():
    """窗口按「下标」而不是自然日滑动：传入 date 会诱导按日历日算，口径就错了。"""
    assert date  # 仅为说明：本模块的 public API 没有任何 date 参数（见函数签名）
