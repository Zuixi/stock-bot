"""连板策略纯函数单测（无 DB、无网络）。

用例编号对应 spec §3 的口径；其中 test_streak_is_one_for_first_board 与
test_suspended_days_do_not_break_streak 是两条曾真实踩过的坑。
"""

from datetime import date

from app.services import limit_up_calculator as calc

D1, D2, D3, D4, D5 = (date(2026, 9, d) for d in (1, 2, 3, 4, 5))


def _row(
    stock_id,
    d,
    *,
    is_lu,
    streak,
    symbol="000001",
    name="测试",
    close=10.0,
    pre_close=9.0,
    open_=9.2,
    amount=1.0,
    sw_l3="110703",
    sw_l3_name="生猪养殖",
    sw_l1="110000",
    sw_l1_name="农林牧渔",
):
    return {
        "stock_id": stock_id,
        "symbol": symbol,
        "name": name,
        "trade_date": d,
        "close": close,
        "open": open_,
        "high": close,
        "amount": amount,
        "pre_close": pre_close,
        "up_limit": 11.0,
        "down_limit": 9.0,
        "is_lu": is_lu,
        "touched": is_lu,
        "streak_upto": streak,
        "sw_l3_code": sw_l3,
        "sw_l3_name": sw_l3_name,
        "sw_l1_code": sw_l1,
        "sw_l1_name": sw_l1_name,
    }


def test_streak_is_one_for_first_board():
    """首板必须是 1。

    反例：向量化写法的 (~is_lu).cumsum() + groupby(...).cumcount() 变体会把整档梯队
    错位一格、首板凭空消失（56 只首板显示成二连板）——这是本功能最致命的一种错法。
    """
    rows = [_row(1, D1, is_lu=True, streak=1)]
    assert [b["streak"] for b in calc.ladder(rows, D1)] == [1]
    assert calc.ladder(rows, D1)[0]["stocks"][0]["symbol"] == "000001"


def test_ladder_groups_by_streak_desc_with_high_tier_label():
    rows = [
        _row(1, D1, is_lu=True, streak=1, symbol="000001"),
        _row(2, D1, is_lu=True, streak=2, symbol="000002"),
        _row(3, D1, is_lu=True, streak=6, symbol="000003"),
        _row(4, D1, is_lu=False, streak=0, symbol="000004"),
    ]
    got = [(b["streak"], b["label"], len(b["stocks"])) for b in calc.ladder(rows, D1)]
    assert got == [(6, "6连板", 1), (2, "2连板", 1), (1, "首板", 1)]


def test_n_day_m_board_counts_market_days_not_stock_rows():
    """N天M板：M=涨停次数，N=市场交易日跨度（不是该股有行情的天数）。"""
    market_days = [D1, D2, D3, D4, D5]
    rows = [_row(1, D3, is_lu=True, streak=1), _row(1, D5, is_lu=True, streak=2)]
    span, boards = calc.n_day_m_board(rows, 1, D5, market_days)
    assert (boards, span) == (2, 3)  # 3天2板


def test_suspended_days_do_not_break_streak():
    """policy A：停牌日不计数也不截断（对齐东财 lbc）。

    实测依据：002274 华昌化工 窗口内 12 个交易日只有 7 天有行情；若把缺行当断板，
    本地数字会与用户拿来对照的第三方不一致。missing_days 必须如实下发。
    """
    market_days = [D1, D2, D3, D4, D5]
    rows = [
        _row(1, D1, is_lu=True, streak=1),
        _row(1, D4, is_lu=True, streak=2),
        _row(1, D5, is_lu=True, streak=3),
    ]
    assert calc.ladder(rows, D5)[0]["stocks"][0]["streak"] == 3
    assert calc.missing_days(rows, 1, market_days) == 2  # 5 个市场日 - 3 天有行情


def test_promotion_rate_is_intersection_based():
    """1进2 的分子必须是交集：昨日首板 且 今日二板。"""
    rows = [
        _row(1, D4, is_lu=True, streak=1),
        _row(1, D5, is_lu=True, streak=2),  # 晋级
        _row(2, D4, is_lu=True, streak=1),
        _row(2, D5, is_lu=False, streak=0),  # 断板
        _row(3, D4, is_lu=True, streak=1),
        _row(3, D5, is_lu=True, streak=1),  # 断后回封
    ]
    promo = calc.promotion_rate(rows, D4, D5, level=1)
    assert promo == {"rate": 1 / 3, "n": 3, "noisy": True}


def test_promotion_rate_none_when_no_denominator():
    assert calc.promotion_rate([], D4, D5, level=1) == {"rate": None, "n": 0, "noisy": True}


def test_sector_ladder_picks_deterministic_leader_and_buckets_unmapped():
    """同板位龙头按 amount DESC, symbol ASC 裁决；映射不到的进 unclassified。"""
    rows = [
        _row(1, D5, is_lu=True, streak=2, symbol="000002", amount=5.0, sw_l3="110703"),
        _row(2, D5, is_lu=True, streak=2, symbol="000001", amount=5.0, sw_l3="110703"),
        _row(
            3,
            D5,
            is_lu=True,
            streak=1,
            symbol="000003",
            amount=9.0,
            sw_l3=None,
            sw_l3_name=None,
            sw_l1=None,
            sw_l1_name=None,
        ),
    ]
    got = calc.sector_ladder(rows, D5)
    assert got["unclassified_count"] == 1
    assert got["items"][0]["l3_name"] == "生猪养殖"
    assert got["items"][0]["leader_symbol"] == "000001"  # 同板同额 → symbol 升序
    assert got["items"][0]["max_streak"] == 2


def test_yesterday_limit_up_computes_today_pct_when_quoted():
    """今日有行情的昨日涨停股：按当日 pre_close 算 today_pct，且不得误判为停牌。"""
    rows = [
        _row(1, D4, is_lu=True, streak=1, pre_close=10.0),
        _row(1, D5, is_lu=False, streak=0, close=11.0, open_=10.5, pre_close=10.0),
    ]
    got = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])
    item = got["items"][0]
    assert item["suspended"] is False
    assert item["today_pct"] == 10.0
    assert item["today_open_premium"] == 5.0
    assert got["kpis"]["yzt_avg_pct"] == 10.0


def test_yesterday_limit_up_reports_suspended_stock_as_null_not_zero():
    """今日停牌的昨日涨停股 today_* 一律 None，且 suspended=True（缺失 ≠ 0%）。

    反例：把无当日行的票默认成 today_pct=0.0，会被当成"平盘"混进均值与榜单且不报错
    （真实 0.00% 与缺失无法区分）；正确口径是排除在 measured 之外。
    """
    rows = [_row(1, D4, is_lu=True, streak=1, pre_close=10.0)]  # D5 停牌：无当日行
    got = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])
    item = got["items"][0]
    assert item["suspended"] is True
    assert item["today_pct"] is None
    assert item["today_open_premium"] is None
    assert item["today_streak"] is None
    assert item["broken"] is False
    assert got["kpis"] == {
        "n": 1,
        "measured": 0,
        "yzt_avg_pct": None,
        "yzt_avg_open_premium": None,
    }


def test_yesterday_pct_uses_today_pre_close_not_prev_row():
    """分母必须是 as_of **当日行**的 pre_close，不是 prev 行。

    取 prev 行 = 「今日 ÷ 前前日」的 2 日收益，静默多算一天。真库实测 2026-09-08：
    当日 pre_close → 2.8225%（95 只），昨日行 pre_close → 13.9488%。
    本用例让两行的 pre_close 不同，使这个错法必然被抓住。
    """
    rows = [
        _row(1, D4, is_lu=True, streak=1, close=10.0, pre_close=9.0),  # 前前日收 9.0
        _row(1, D5, is_lu=False, streak=0, close=10.5, open_=10.2, pre_close=10.0),
    ]
    item = calc.yesterday_limit_up(rows, D4, D5, [D4, D5])["items"][0]
    assert item["today_pct"] == 5.0  # (10.5-10.0)/10.0，而不是 (10.5-9.0)/9.0
    assert item["today_open_premium"] == 2.0
    assert item["broken"] is False  # 每个 item 都必须带 broken 键（停牌股也不例外）


def test_sentiment_kpis_broken_rate_uses_market_breadth():
    breadth = {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5490}
    kpis = calc.sentiment_kpis(
        breadth,
        {"yzt_avg_pct": 2.82, "yzt_avg_open_premium": 1.1, "n": 95},
        {"rate": 0.159, "n": 82, "noisy": False},
        {"rate": 0.31, "n": 13, "noisy": False},
        [{"streak": 4}],
    )
    assert kpis["zt_count"] == 75
    assert kpis["broken_rate"] == round(39 / 114, 4)
    assert kpis["max_streak"] == 4
    assert kpis["promo_1to2"] == 0.159


def test_is_partial_uses_quote_floor():
    assert calc.is_partial({"quoted": 5490}) is False
    assert calc.is_partial({"quoted": 12}) is True
