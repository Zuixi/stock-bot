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
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

logger = logging.getLogger(__name__)

SNAPSHOT_TTL = 300
CALENDAR_TTL = 900
# 派生表（market_sentiment_daily）写入后须失效的读缓存键模式：失效内聚在
# persist_snapshot 内，任何调用方（定时/手动/对账）都不依赖"记得清缓存"。
_CALENDAR_CACHE_PATTERN = "market:limit-up:calendar:*"
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
        if not rows:
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
        item["seal_time"] = None  # 本地路径不具备（Web 增强在 Task 6 注入）
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
    if snap["degraded_reason"] is None:
        snap = await enrich_from_web(snap, target.strftime("%Y%m%d"))
    if cache is not None and snap["degraded_reason"] is None:
        await cache.set(key, snap, ttl=SNAPSHOT_TTL)
    return snap


async def _fetch_web_pool(trade_date: str) -> list[dict[str, Any]]:
    from app.core.providers.eastmoney_client import get_eastmoney_client  # noqa: PLC0415

    return await get_eastmoney_client().fetch_limit_up_pool(trade_date)


def apply_web_enrichment(
    snapshot: dict[str, Any], web_rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """把 Web 的封板时间/封单/炸板次数注入匹配 symbol；未匹配保持 None。"""
    by_symbol = {r["symbol"]: r for r in web_rows}
    for echelon in snapshot.get("echelons", []):
        for stock in echelon["stocks"]:
            web = by_symbol.get(stock["symbol"])
            if web is None:
                continue
            stock["seal_time"] = web["seal_time"]
            stock["seal_fund"] = web["seal_fund"]
            stock["break_count"] = web["break_count"]
    return snapshot


async def enrich_from_web(snapshot: dict[str, Any], trade_date: str) -> dict[str, Any]:
    """附加封板信息（增强字段只能来自 Web）；任何失败仅 log，主路径状态不变。

    取数按 **as_of**（不是"今天"）：daily_quotes 只回补到上一个工作日，
    `latest_quote_date` 永远不是今天，所以 `target == 今日 && 盘中` 的触发条件恒为假（死代码）；
    且该端点支持历史日期，按 as_of 取数既正确又不需要交易时段判断。
    """
    if not snapshot.get("echelons") or snapshot.get("degraded_reason"):
        return snapshot
    try:
        web_rows = await _fetch_web_pool(trade_date)
    except Exception:  # noqa: BLE001 —— 增强失败不得影响主路径
        logger.warning("limit-up web enrichment failed; seal fields stay null", exc_info=True)
        return snapshot
    return apply_web_enrichment(snapshot, web_rows)


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


async def _invalidate_calendar_cache() -> int:
    """失效情绪周期读缓存（Redis 不可用时 delete_pattern 内部静默返回 0）。

    calendar 缓存"非空即缓存"会把旧结果集固化（TTL 900s 内补数也不可见），
    实测 9/17 补完两日数据后日历端点仍只返回 1 点——失效必须与落库同点发生。
    """
    from app.core.redis import CacheClient, get_redis_pool  # noqa: PLC0415

    cache = CacheClient(await get_redis_pool())
    return await cache.delete_pattern(_CALENDAR_CACHE_PATTERN)


async def persist_snapshot(
    db: AsyncSession | None, cache: CacheClient | None, as_of: date | None = None
) -> dict[str, Any]:
    """盘后把当日情绪聚合写入派生表（幂等，可重复跑）。

    is_partial / 限价缺失 / 空候选 / 无行情一律不写：时序图里塞进一个假低谷比缺一天更糟；
    `no_limit_up_rows`（快照 kpis 为空）漏在名单外会直接 `k["zt_count"]` KeyError。
    """
    snap = await get_snapshot(cache, as_of)
    # 部分行情日优先按 partial_day 跳过：get_snapshot 在「候选窗口为空」时先报
    # no_limit_up_rows，但部分 ingest 才是更本质的跳过原因（数据没拉全，而非真的没涨停）。
    if snap.get("is_partial"):
        return {"status": "skipped", "reason": "partial_day"}
    if snap["degraded_reason"] in (
        "price_limits_missing",
        "partial_day",
        "no_quotes",
        "insufficient_trade_days",
        "no_limit_up_rows",
    ):
        return {"status": "skipped", "reason": snap["degraded_reason"]}
    k = snap["kpis"]
    leaders = snap["echelons"][0]["stocks"] if snap["echelons"] else []
    # 缓存命中时 snap 是 Redis JSON 回读的，as_of 已变成 str；直接落库会抛
    # asyncpg `'str' object has no attribute 'toordinal'`（真库复现过）。
    trade_date = date.fromisoformat(str(snap["as_of"]))
    row = {
        "trade_date": trade_date,
        "zt_count": k["zt_count"],
        "dt_count": k["dt_count"],
        "zb_count": k["zb_count"],
        "broken_rate": k["broken_rate"],
        "yzt_avg_pct": k["yzt_avg_pct"],
        "promo_1to2": k["promo_1to2"],
        "promo_1to2_n": k["promo_1to2_n"],
        "promo_2to3": k["promo_2to3"],
        "promo_2to3_n": k["promo_2to3_n"],
        "max_streak": k["max_streak"],
        "max_streak_symbol": leaders[0]["symbol"] if leaders else None,
        "source": snap["source"],
    }
    if db is None:
        async with async_session_factory() as session:
            await limit_up_repo.upsert_sentiment_daily(session, row)
            await session.commit()
    else:
        await limit_up_repo.upsert_sentiment_daily(db, row)
    await _invalidate_calendar_cache()
    return {"status": "ok", "trade_date": trade_date, "zt_count": k["zt_count"]}


async def get_calendar(cache: CacheClient | None, days: int = 30) -> list[dict[str, Any]]:
    """情绪周期时序（来自 `market_sentiment_daily` 派生缓存，升序）。

    缓存 key 含 `days`（改变结果集的维度）；空结果不缓存，避免把"还没落库"固化成空图。
    """
    key = f"market:limit-up:calendar:{days}"
    if cache is not None:
        cached: list[dict[str, Any]] | None = await cache.get(key)
        if cached:
            return cached
    async with async_session_factory() as db:
        rows = await limit_up_repo.list_sentiment_calendar(db, days)
    if cache is not None and rows:
        await cache.set(key, rows, ttl=CALENDAR_TTL)
    return rows
