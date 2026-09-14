"""连板/情绪策略纯函数——无 DB、无网络、无时钟依赖，全部可单测。

口径见 docs/design/limit-up-sentiment.md §3。这里只放"策略"，聚合已在 SQL 里做完
（gaps-and-islands 实测与逐行状态机同结果），因此本模块不重算连板。
"""

from __future__ import annotations

from datetime import date
from typing import Any

LOOKBACK_TRADE_DAYS = 10
PARTIAL_QUOTE_FLOOR = 4000  # 实测正常交易日 5,490 行/日
NOISY_PROMOTION_N = 5


def _today_rows(window_rows: list[dict[str, Any]], as_of: date) -> dict[int, dict[str, Any]]:
    return {r["stock_id"]: r for r in window_rows if r["trade_date"] == as_of}


def _limit_up_rows(window_rows: list[dict[str, Any]], d: date) -> list[dict[str, Any]]:
    return [r for r in window_rows if r["trade_date"] == d and r["is_lu"]]


def missing_days(window_rows: list[dict[str, Any]], stock_id: int, market_days: list[date]) -> int:
    """该股在窗口内缺失的交易日数（停牌/无行情），UI 用来披露停牌股。"""
    have = {r["trade_date"] for r in window_rows if r["stock_id"] == stock_id}
    return len([d for d in market_days if d not in have])


def n_day_m_board(
    window_rows: list[dict[str, Any]],
    stock_id: int,
    as_of: date,
    market_days: list[date],
    lookback: int = LOOKBACK_TRADE_DAYS,
) -> tuple[int, int]:
    """(days_span, boards_in_window)：最近 lookback 个市场交易日内的涨停跨度与次数。

    policy A：只数该股真实有行情的交易日；停牌日不计数也不截断。
    """
    window = [d for d in market_days if d <= as_of][-lookback:]
    allowed = set(window)
    lu_days = [
        r["trade_date"]
        for r in window_rows
        if r["stock_id"] == stock_id and r["is_lu"] and r["trade_date"] in allowed
    ]
    if not lu_days:
        return (0, 0)
    first = min(lu_days)
    span = len([d for d in market_days if first <= d <= as_of])
    return (span, len(lu_days))


def _label(streak: int) -> str:
    return "首板" if streak <= 1 else f"{streak}连板"


def ladder(window_rows: list[dict[str, Any]], as_of: date) -> list[dict[str, Any]]:
    """当日涨停按连板数降序分档（含首板），每档内按成交额降序、代码升序。"""
    rows = _limit_up_rows(window_rows, as_of)
    buckets: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        buckets.setdefault(int(r["streak_upto"]), []).append(r)
    out: list[dict[str, Any]] = []
    for streak in sorted(buckets, reverse=True):
        stocks = [
            # 每只股票带规范名 `streak`（= as_of 的 streak_upto）；保留原始 `streak_upto`
            # 供服务层投影（服务层按 streak 展示、按 streak_upto 消费）。
            {**r, "streak": int(r["streak_upto"])}
            for r in sorted(buckets[streak], key=lambda r: (-(r["amount"] or 0.0), r["symbol"]))
        ]
        out.append({"streak": streak, "label": _label(streak), "stocks": stocks})
    return out


def sector_ladder(window_rows: list[dict[str, Any]], as_of: date) -> dict[str, Any]:
    """申万 L3 维度的最高板排行；未映射标的进 unclassified_count 兜底桶。

    龙头裁决必须确定性（streak DESC, amount DESC, symbol ASC），否则两次相同请求
    之间「龙头」会跳变。
    """
    rows = _limit_up_rows(window_rows, as_of)
    groups: dict[str, list[dict[str, Any]]] = {}
    unclassified = 0
    for r in rows:
        code = r["sw_l3_code"]
        if code is None:
            unclassified += 1
            continue
        groups.setdefault(code, []).append(r)

    items: list[dict[str, Any]] = []
    for code, members in groups.items():
        leader = min(
            members,
            key=lambda r: (-int(r["streak_upto"]), -(r["amount"] or 0.0), r["symbol"]),
        )
        items.append(
            {
                "l3_code": code,
                "l3_name": members[0]["sw_l3_name"],
                "l1_code": members[0]["sw_l1_code"],
                "l1_name": members[0]["sw_l1_name"],
                "max_streak": int(leader["streak_upto"]),
                "leader_symbol": leader["symbol"],
                "leader_name": leader["name"],
                "leader_streak": int(leader["streak_upto"]),
                "zt_count": len(members),
            }
        )
    items.sort(key=lambda i: (-i["max_streak"], -i["zt_count"], i["l3_code"]))
    return {"items": items, "unclassified_count": unclassified}


def promotion_rate(
    window_rows: list[dict[str, Any]], as_of_prev: date, as_of: date, level: int
) -> dict[str, Any]:
    """level 进 level+1 的晋级率：分子是「昨日 level 板 ∩ 今日 level+1 板」。"""
    prev = {
        r["stock_id"]
        for r in window_rows
        if r["trade_date"] == as_of_prev and r["is_lu"] and int(r["streak_upto"]) == level
    }
    today = _today_rows(window_rows, as_of)
    promoted = sum(
        1
        for s in prev
        if (t := today.get(s)) is not None and t["is_lu"] and int(t["streak_upto"]) == level + 1
    )
    n = len(prev)
    return {
        "rate": (promoted / n) if n else None,
        "n": n,
        "noisy": n < NOISY_PROMOTION_N,
    }


def yesterday_limit_up(
    window_rows: list[dict[str, Any]],
    as_of_prev: date,
    as_of: date,
    market_days: list[date],
) -> dict[str, Any]:
    """昨日涨停股今日表现 + 赚钱效应 KPI。

    涨跌幅基准必须取 **as_of 当日行**的 `pre_close`（= 上一交易日收盘，交易所口径、
    已含除权调整），不是 prev 行的。取 prev 行会变成「今日 ÷ 前前日」的 2 日收益：
    实测 2026-09-08（95 只昨日涨停股）从真值 2.8225% 变成 13.9488%，5 倍偏差且不报错。
    今日停牌的票 today_* 一律 None（缺失 ≠ 0%）。
    """
    prev = _limit_up_rows(window_rows, as_of_prev)
    today = _today_rows(window_rows, as_of)

    items: list[dict[str, Any]] = []
    pcts: list[float] = []
    premiums: list[float] = []
    for p in prev:
        t = today.get(p["stock_id"])
        pre = None if t is None or t["pre_close"] is None else float(t["pre_close"])
        item: dict[str, Any] = {
            "symbol": p["symbol"],
            "name": p["name"],
            "prev_streak": int(p["streak_upto"]),
            "sw_l3_name": p["sw_l3_name"],
            "missing_days": missing_days(window_rows, p["stock_id"], market_days),
            "today_pct": None,
            "today_open_premium": None,
            "today_streak": None,
            "is_lu": False,
            "touched": False,
            "broken": False,
            "suspended": t is None,
        }
        if t is not None and pre is not None:
            close = float(t["close"]) if t["close"] is not None else None
            open_ = float(t["open"]) if t["open"] is not None else None
            item["today_pct"] = None if close is None else round((close - pre) / pre * 100, 4)
            item["today_open_premium"] = (
                None if open_ is None else round((open_ - pre) / pre * 100, 4)
            )
            item["today_streak"] = int(t["streak_upto"]) if t["is_lu"] else 0
            item["is_lu"] = bool(t["is_lu"])
            item["touched"] = bool(t["touched"])
            item["broken"] = bool(t["touched"]) and not bool(t["is_lu"])
            if item["today_pct"] is not None:
                pcts.append(item["today_pct"])
            if item["today_open_premium"] is not None:
                premiums.append(item["today_open_premium"])
        items.append(item)

    # 缺失值（None / 停牌）必须排在最后：不能写 `or -999`——那会把真实的 0.00% 当成缺失。
    items.sort(
        key=lambda i: (
            -(i["prev_streak"]),
            -(i["today_pct"] if i["today_pct"] is not None else -999.0),
            i["symbol"],
        )
    )
    kpis = {
        "n": len(prev),
        "measured": len(pcts),
        "yzt_avg_pct": round(sum(pcts) / len(pcts), 4) if pcts else None,
        "yzt_avg_open_premium": round(sum(premiums) / len(premiums), 4) if premiums else None,
    }
    return {"kpis": kpis, "items": items}


def sentiment_kpis(
    breadth: dict[str, int],
    yzt: dict[str, Any],
    promo_1to2: dict[str, Any],
    promo_2to3: dict[str, Any],
    ladder_items: list[dict[str, Any]],
) -> dict[str, Any]:
    """情绪温度计：广度三计数 + 赚钱效应 + 晋级率 + 空间板高度。"""
    zt, zb = breadth["zt_count"], breadth["zb_count"]
    return {
        "zt_count": zt,
        "dt_count": breadth["dt_count"],
        "zb_count": zb,
        "broken_rate": round(zb / (zt + zb), 4) if (zt + zb) else None,
        "yzt_avg_pct": yzt["yzt_avg_pct"],
        "yzt_avg_open_premium": yzt["yzt_avg_open_premium"],
        "yzt_n": yzt["n"],
        "promo_1to2": promo_1to2["rate"],
        "promo_1to2_n": promo_1to2["n"],
        "promo_1to2_noisy": promo_1to2["noisy"],
        "promo_2to3": promo_2to3["rate"],
        "promo_2to3_n": promo_2to3["n"],
        "promo_2to3_noisy": promo_2to3["noisy"],
        "max_streak": max((b["streak"] for b in ladder_items), default=0),
    }


def is_partial(breadth: dict[str, int]) -> bool:
    """当日行情行数不足 → 数据不完整（部分 ingest），不得当作完整梯队展示。"""
    return breadth["quoted"] < PARTIAL_QUOTE_FLOOR
