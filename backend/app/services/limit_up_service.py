"""连板梯队与市场情绪：快照编排（唯一带 try/except 的层）。

source 由**数据可用性**决定，不由偏好决定：有完整限价 + 完整当日行情 → 本地自算（权威、
可回放）；否则返回空 payload + degraded_reason，**不猜**。读路径只读，绝不外呼写库。
"""

from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING, Any

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.services import limit_up_calculator as calc

if TYPE_CHECKING:
    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 300
CALENDAR_TTL = 900
_WINDOW_BUFFER = 6  # gaps-and-islands 需要的前置上下文（lookback 之外多取的交易日）


def window_trade_days(lookback: int) -> int:
    """窗口交易日数必须随 lookback 增长。

    硬编码 16 会让 lookback=30（端点允许）静默截断：streak 被窗口起点截平、
    N天M板数不到 lookback。key 已包含 lookback，所以窗口大小变化不会污染缓存。
    """
    return lookback + _WINDOW_BUFFER


def _empty(as_of: date | None, reason: str) -> dict[str, Any]:
    return {
        "as_of": as_of,
        "as_of_prev": None,
        "source": "local_calc",
        "limits_present": False,
        "is_partial": False,
        "sw_coverage": None,
        "degraded_reason": reason,
        "market_days": [],
        "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
        "kpis": {},
        "echelons": [],
        "sectors": {"items": [], "unclassified_count": 0},
        "yesterday": {"kpis": {}, "items": []},
    }


async def get_snapshot(
    cache: CacheClient | None, as_of: date | None = None, lookback: int = calc.LOOKBACK_TRADE_DAYS
) -> dict[str, Any]:
    """一次查询 → 一份快照 → 3 个端点共享，保证口径同源。"""
    async with async_session_factory() as db:
        target = as_of or await limit_up_repo.latest_quote_date(db)
        if target is None:
            return _empty(None, "no_quotes")
        key = f"market:limit-up:snapshot:{target.isoformat()}:{lookback}"
        if cache is not None:
            cached: dict[str, Any] | None = await cache.get(key)
            if cached:
                return cached
        market_days = await limit_up_repo.list_recent_trade_dates(
            db, target, window_trade_days(lookback)
        )
        if len(market_days) < 2:
            return _empty(target, "insufficient_trade_days")
        breadth = await limit_up_repo.fetch_day_breadth(db, target)
        limits_present = await limit_up_repo.has_price_limits(db, target)
        snap = _empty(target, None)  # type: ignore[arg-type]
        snap["as_of_prev"] = market_days[-2]
        snap["limits_present"] = limits_present
        snap["is_partial"] = calc.is_partial(breadth)
        snap["breadth"] = breadth
        snap["market_days"] = market_days
        if not limits_present:
            snap["degraded_reason"] = "price_limits_missing"
            return snap
        rows = await limit_up_repo.fetch_limit_up_window(
            db, as_of=target, as_of_prev=market_days[-2], window_start=market_days[0]
        )
        # 「候选为空」的判据是当日无涨停（breadth.zt_count==0），不是窗口行数：
        # 完整行情日的 kpis 来自 breadth、与窗口行无关，不能因窗口为空就整份丢弃。
        if not rows and breadth["zt_count"] == 0:
            snap["degraded_reason"] = "no_limit_up_rows"
            return snap

    echelons = calc.ladder(rows, target)
    ladder_pcts = [
        {
            "stock_id": s["stock_id"],
            "symbol": s["symbol"],
            "name": s["name"],
            "streak": s["streak_upto"],
            "sw_l3_name": s["sw_l3_name"],
            "sw_l1_name": s["sw_l1_name"],
        }
        for b in echelons
        for s in b["stocks"]
    ]
    for item in ladder_pcts:
        span, boards = calc.n_day_m_board(rows, item["stock_id"], target, market_days, lookback)
        item["days_span"] = span
        item["boards_in_window"] = boards
        item["missing_days"] = calc.missing_days(rows, item["stock_id"], market_days)
        item["seal_time"] = None      # 本地路径不具备（Web 增强在 Task 6 注入）
        item["seal_fund"] = None
        item["break_count"] = None
    yesterday = calc.yesterday_limit_up(rows, market_days[-2], target, market_days)
    by_id = {i["stock_id"]: i for i in ladder_pcts}
    snap = {
        **snap,
        "echelons": [
            {
                "streak": b["streak"],
                "label": b["label"],
                "stocks": [by_id[s["stock_id"]] for s in b["stocks"]],
            }
            for b in echelons
        ],
        "sectors": calc.sector_ladder(rows, target),
        "yesterday": yesterday,
        "kpis": calc.sentiment_kpis(
            breadth,
            yesterday["kpis"],
            calc.promotion_rate(rows, market_days[-2], target, level=1),
            calc.promotion_rate(rows, market_days[-2], target, level=2),
            [{"streak": b["streak"]} for b in echelons],
        ),
    }
    mapped = sum(1 for i in ladder_pcts if i["sw_l3_name"])
    snap["sw_coverage"] = round(mapped / len(ladder_pcts), 4) if ladder_pcts else None
    if snap["is_partial"] and snap["degraded_reason"] is None:
        snap["degraded_reason"] = "partial_day"
    if cache is not None and snap["degraded_reason"] is None:
        await cache.set(key, snap, ttl=SNAPSHOT_TTL)
    return snap


def sector_payload(snap: dict[str, Any], sw_l1: str | None = None) -> dict[str, Any]:
    """申万 L3 最高板投影；`sw_l1` 过滤为客户端维度，缓存不受其影响。"""
    items = snap["sectors"]["items"]
    if sw_l1:
        items = [i for i in items if i["l1_code"] == sw_l1]
    return {
        "as_of": snap["as_of"],
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "unclassified_count": snap["sectors"]["unclassified_count"],
        "items": items,
    }


def yesterday_payload(snap: dict[str, Any]) -> dict[str, Any]:
    """昨日涨停今日表现投影。"""
    return {
        "as_of": snap["as_of"],
        "as_of_prev": snap["as_of_prev"],
        "source": snap["source"],
        "degraded_reason": snap["degraded_reason"],
        "kpis": snap["yesterday"]["kpis"],
        "items": snap["yesterday"]["items"],
    }
