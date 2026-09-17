"""Market service: provide market dashboard data for frontend.

Data sources
------------
- **PostgreSQL**: ``index_dailies`` for main indices, ``stocks`` + ``daily_quotes``
  for aggregated stats.
- **Redis**: short-lived cache (300s) for all dashboard endpoints.
- **TuShare Pro** (via ``TuShareClient``): fallback for indices when DB is empty.

"最新交易日"的唯一判据是 :mod:`app.services.market_day_service`（行数 + pct_chg
非空率 + 限价存在）。本模块不再直接 ``max(daily_quotes.trade_date)``：
``_latest_trade_date`` 是它的无缓存薄封装（返回 ``None``）。判据的 Redis 缓存由
判据模块统一持有（``market:day:latest_complete``，TTL 60s）；旧的
``market:latest_trade_date`` / 300s 缓存已下线。

按日结果缓存的键**一律带解析出的 ``as_of``**（``market:sectors:{day}`` 等，见
:func:`_day_cache_key`）：补数/翻日之后旧 payload 不会再用旧标签冒充新一天，
最坏情况从"5 分钟内返回错日"收敛为"下一次请求重新解析"。缓存读取因此排在
解析日之后（解析本身有 60s 共享缓存，不是额外打库）。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Literal, cast

from sqlalchemy import Subquery, TextClause, func, select, text, union
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.redis import CacheClient
from app.models.stock import Stock
from app.schemas.ranking import RankingItemOut, RankingResponseOut, RankingType
from app.schemas.stock import StockOut
from app.schemas.sw_performance import SwPerformanceItemOut, SwPerformanceResponseOut
from app.services import market_day_service
from app.services.market_data_service import _today_sh
from app.services.market_snapshot_service import group_by, load_day_rows, summarize_group

logger = logging.getLogger(__name__)

HotBoardCategory = Literal["industry", "concept", "region"]

#: 按日结果缓存的 TTL（秒）。**与前端 staleTime 对齐**：首页按日聚合卡片
#: （RankingMatrix / SectorHeatmap / NorthboundCard 等）统一 ``staleTime = 5*60_000``，
#: 服务端 300s 与之同频，客户端过期时服务端条目也正好到期，不会出现"前端已刷新、
#: 服务端仍回放旧 payload"的错位。键里带 ``as_of``（见 :func:`_day_cache_key`），
#: 故补数/翻日只会让新键 miss，不会让旧标签继续冒充。
_MARKET_CACHE_TTL = 300  # 5 minutes
_SW_OTHER_LEVEL1_CODE = "OTHER"
_SW_OTHER_LEVEL1_NAME = "其他"
_SW_OTHER_UNKNOWN_INDUSTRY = "未知行业"

# ---------------------------------------------------------------------------
# Target index codes for the main dashboard
# ---------------------------------------------------------------------------

_TARGET_INDICES: list[dict[str, str]] = [
    {"ts_code": "000001.SH", "name": "上证指数", "exchange": "Shanghai_Stocks"},
    {"ts_code": "399001.SZ", "name": "深证成指", "exchange": "Shenzen_Stocks"},
    {"ts_code": "399006.SZ", "name": "创业板指", "exchange": "Shenzen_Stocks"},
    {"ts_code": "899050.BJ", "name": "北证50", "exchange": "Beijing_Stocks"},
    {"ts_code": "000016.SH", "name": "上证50", "exchange": "Shanghai_Stocks"},
    {"ts_code": "000300.SH", "name": "沪深300", "exchange": "Shanghai_Stocks"},
]

INDEX_NAME_MAP: dict[str, str] = {idx["ts_code"]: idx["name"] for idx in _TARGET_INDICES}
INDEX_EXCHANGE_MAP: dict[str, str] = {idx["ts_code"]: idx["exchange"] for idx in _TARGET_INDICES}

# ---------------------------------------------------------------------------
# Helper: latest trade date = completeness-predicate delegate (single source)
# ---------------------------------------------------------------------------


async def _latest_trade_date(db: AsyncSession) -> date | None:
    """Return the resolved latest trade date, or ``None`` when daily_quotes is empty.

    Thin **uncached** delegate to :func:`market_day_service.resolve_latest_complete_day`
    so the "which day is usable" decision exists in exactly one place. Callers that
    also need the quality label call the resolver directly (see the ``get_*`` readers).
    """
    md = await market_day_service.resolve_latest_complete_day(db)
    return md.day if md is not None else None


async def _latest_trade_date_str() -> str:
    """Return latest trade date as YYYYMMDD for TuShare calls."""
    async with async_session_factory() as db:
        d = await _latest_trade_date(db)
    if d:
        return d.strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


# ---------------------------------------------------------------------------
# "as of" helpers — the completeness predicate is the only source of truth
# ---------------------------------------------------------------------------


def last_weekday(d: date) -> date:
    """Most recent weekday <= d.

    Phase-2 heuristic ONLY — exchange holidays are NOT handled. Phase 4 swaps
    this for a ``trade_calendar`` lookup behind the same signature.
    """
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _envelope(
    md: market_day_service.MarketDay | None, rows: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the ``MarketListOut`` payload; empty DB degrades to ``as_of=None``.

    ``reason`` is only populated when the resolved day is a fallback/partial pick
    (see Task 1's ``MarketDay``); branch callers on ``as_of_quality``, never on ``reason``.
    """
    return {
        "as_of": md.day.isoformat() if md is not None else None,
        "as_of_quality": md.quality if md is not None else "partial",
        "as_of_reason": md.reason if md is not None else None,
        "items": rows,
    }


def _day_cache_key(prefix: str, md: market_day_service.MarketDay | None) -> str:
    """``{prefix}:{as_of}`` —— 每个解析出的交易日一份结果缓存。

    Task 8 之前这些键不含日期（``market:sectors`` 等），补数或翻日之后旧 payload
    最长 300s 仍会带着旧的 ``as_of``/``as_of_quality`` 被回放。日期进键后，新一天
    必然 miss 并重算，旧 key 只会在 TTL 内自然过期（不读、不清理）。
    ``md is None`` 的调用方（空库降级）本就不读/不写缓存，``none`` 后缀只是不变量。
    """
    return f"{prefix}:{md.day.isoformat() if md is not None else 'none'}"


# ---------------------------------------------------------------------------
# Public async service methods
# ---------------------------------------------------------------------------


async def list_market_indices(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """Return main market index snapshots from DB (index_dailies).

    Falls back to TuShare if DB has no data.
    """
    cache_key = "market:indices"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cast(list[dict[str, Any]], cached)

    from app.repositories import index_repo  # noqa: PLC0415

    ts_codes = [idx["ts_code"] for idx in _TARGET_INDICES]
    async with async_session_factory() as db:
        rows = await index_repo.get_latest(db, ts_codes)

    results: list[dict[str, Any]] = []
    if rows:
        for row in rows:
            close = float(row.close)
            pre_close = float(row.pre_close) if row.pre_close else 0
            change = round(close - pre_close, 2) if pre_close else 0
            change_pct = round(change / pre_close * 100, 2) if pre_close else 0
            td = row.trade_date
            asof = f"{td.year:04d}-{td.month:02d}-{td.day:02d}T15:00:00Z"
            results.append(
                {
                    "code": row.ts_code.split(".")[0],
                    "tsCode": row.ts_code,
                    "name": INDEX_NAME_MAP.get(row.ts_code, row.ts_code),
                    "value": round(close, 2),
                    "change": change,
                    "changePercent": change_pct,
                    "exchange": INDEX_EXCHANGE_MAP.get(row.ts_code, ""),
                    "asof": asof,
                }
            )
        # Preserve the order defined in _TARGET_INDICES
        order = {idx["ts_code"]: i for i, idx in enumerate(_TARGET_INDICES)}
        results.sort(key=lambda r: order.get(r["tsCode"], 999))
    else:
        results = await _fetch_indices_from_tushare()

    if cache and results:
        await cache.set(cache_key, results, _MARKET_CACHE_TTL)
    return results


async def _fetch_indices_from_tushare() -> list[dict[str, Any]]:
    """Fallback: fetch index snapshots directly from TuShare API."""
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    try:
        client = get_tushare_client()
    except Exception:
        logger.warning("list_market_indices: TuShare client unavailable")
        return []

    results: list[dict[str, Any]] = []
    for idx in _TARGET_INDICES:
        try:
            df = await client.fetch_index_daily(
                ts_code=idx["ts_code"],
                start_date="",
                end_date="",
            )
            if df.empty:
                continue
            row = df.sort_values("trade_date", ascending=False).iloc[0]
            close = float(row.get("close", 0))
            pre_close = float(row.get("pre_close", 0))
            change = round(close - pre_close, 2) if pre_close else 0
            change_pct = round(change / pre_close * 100, 2) if pre_close else 0
            trade_date = str(row.get("trade_date", ""))
            asof = (
                f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}T15:00:00Z"
                if len(trade_date) == 8
                else None
            )
            results.append(
                {
                    "code": idx["ts_code"].split(".")[0],
                    "tsCode": idx["ts_code"],
                    "name": idx["name"],
                    "value": round(close, 2),
                    "change": change,
                    "changePercent": change_pct,
                    "exchange": idx["exchange"],
                    "asof": asof,
                }
            )
        except Exception:
            logger.warning(
                "_fetch_indices_from_tushare: failed for %s", idx["ts_code"], exc_info=True
            )

    return results


# ---------------------------------------------------------------------------
# Day-aggregated endpoints — one shared day snapshot (Task 7)
#
# Every function below used to run its **own** "latest day + join stocks + group"
# statement, each carrying a per-stock ``LEFT JOIN LATERAL`` that recomputed the
# previous close to derive ``pct_chg``. They now share ONE
# :func:`market_snapshot_service.load_day_rows` call (plus that module's 300s Redis
# entry) and do the bucketing/grouping in Python over the same rows.
#
# The win is **page-level**, and it is modest (Task 7 measured, day 2026-09-16, n=5
# page medians): a page rendering all five costs 217ms → 165ms cold and 1.4ms → 1.4ms
# warm (the endpoint result caches already covered the warm path), while the Postgres
# statements behind it drop from 30 (pre-rewrite) / 27 (319fa90, whose four endpoint
# resolvers were still called bare) to **7**: one 5-statement completeness probe, one
# full-market row load, one SW rollup.
#
# No single-query speedup is claimed: measured in isolation, every endpoint was
# already slower than its retired statement (pre-rewrite endpoint cold times were
# distribution 198ms, sectors 64ms, capital-flow 47ms, hot-boards 52ms; the shared
# loader alone is 76ms of SQL or a Redis parse of the full payload). The work moved
# from Postgres to the API process rather than disappearing — the loader runs heavier
# than any one retired statement because it returns all ~5,485 rows (see the loader's
# module docstring).
#
# Task 8 re-measured the same day after slimming the row contract to the columns the
# four endpoints actually read (6 instead of 11): payload 1,275 KiB → 761 KiB, Redis
# parse 14.4ms → 8.3ms, loader cache-hit ~14ms → ~9ms, page cold 172ms → 145ms.
# The endpoint result caches still cover the warm page (both ~1-2ms).
#
# All four resolvers below are called ``cache=cache``. A bare call re-runs the
# 5-statement completeness probe and re-reads ``market:day:latest_complete`` once per
# endpoint, which is what made the page issue ~27 statements instead of 7.
# ---------------------------------------------------------------------------

#: Distribution buckets in display order. Boundaries mirror the retired SQL CASE
#: chain exactly (``<= -9.5`` 跌停 / ``>= 9.5`` 涨停; the rest on the integer edges,
#: and ``[5, 9.5)`` is the CASE's trailing ELSE ``>5%``).
_DISTRIBUTION_RANGES: tuple[str, ...] = (
    "跌停",
    ">-7%",
    "-5~-7%",
    "-3~-5%",
    "-1~-3%",
    "0~-1%",
    "0~1%",
    "1~3%",
    "3~5%",
    ">5%",
    "涨停",
)


def _distribution_bucket(pct_chg: float | None) -> str:
    """Bucket one ``pct_chg`` in the retired SQL CASE's own evaluation order.

    Order matters: the negative side tests ``<= -9.5`` before ``< -7``, so -9.7 lands
    in 跌停 while -8.0 lands in ``>-7%`` (the label is the legacy SQL's, kept as-is so
    the frontend contract does not move).

    ``None`` (no usable ``pct_chg``) maps to ``0~1%`` — exactly where the retired
    SQL's ``ELSE 0`` coercion put it, and consistent with the shared summarizer's
    convention that a missing ``pct_chg`` counts as flat.
    """
    if pct_chg is None:
        return "0~1%"
    if pct_chg <= -9.5:
        return "跌停"
    if pct_chg < -7:
        return ">-7%"
    if pct_chg < -5:
        return "-5~-7%"
    if pct_chg < -3:
        return "-3~-5%"
    if pct_chg < -1:
        return "-1~-3%"
    if pct_chg < 0:
        return "0~-1%"
    if pct_chg < 1:
        return "0~1%"
    if pct_chg < 3:
        return "1~3%"
    if pct_chg < 5:
        return "3~5%"
    if pct_chg < 9.5:
        return ">5%"
    return "涨停"


def _distribution_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Count the day's rows into the 11 fixed buckets (zero-filled, display order)."""
    counts: dict[str, int] = dict.fromkeys(_DISTRIBUTION_RANGES, 0)
    for row in rows:
        counts[_distribution_bucket(row.get("pct_chg"))] += 1
    return [{"range": label, "count": counts[label]} for label in _DISTRIBUTION_RANGES]


async def get_distribution(cache: CacheClient | None = None) -> dict[str, Any]:
    """Return market-wide up/down distribution as ``{as_of, as_of_quality, items}``.

    Cache key carries the resolved day (``market:distribution:{as_of}``).
    """
    async with async_session_factory() as db:
        md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
        cache_key = _day_cache_key("market:distribution", md)
        if md is not None and cache:
            cached = await cache.get(cache_key)
            if isinstance(cached, dict):  # bare-list payloads from before this change = miss
                return cast(dict[str, Any], cached)
        rows = await load_day_rows(db, md.day, cache=cache) if md is not None else []

    # No resolved day → ``items: []`` (an unresolved day must not render as eleven
    # zero-count buckets). A resolved day always emits all 11, zero-filled — the shape
    # the retired distribution statement had.
    payload = _envelope(md, _distribution_items(rows) if md is not None else [])
    if cache and md is not None:
        await cache.set(cache_key, payload, _MARKET_CACHE_TTL)
    return payload


def _sector_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """CSRC-industry performance from the snapshot rows, top 30 by average change.

    Same item keys as the retired SQL (``totalMarketCap`` is still the day's turnover
    ``amount`` × 1000 for the existing 千元 → 元 conversion). Sorted by the
    display-rounded mean: rounding is monotone, so this orders exactly like the SQL's
    ``ORDER BY avg_change_pct DESC`` except for rounding ties, which Python's stable
    sort resolves deterministically by first appearance (the loader's ``stock_id``
    order) instead of Postgres' arbitrary one.
    """
    items: list[dict[str, Any]] = []
    for name, group in group_by(rows, "csrc_desc").items():
        stats = summarize_group(group)
        items.append(
            {
                "name": name,
                "changePercent": round(stats["avg_chg"], 2),
                "totalMarketCap": float(sum(r.get("amount") or 0.0 for r in group)) * 1000,
                "stockCount": stats["total"],
                "topStocks": [],
            }
        )
    items.sort(key=lambda item: item["changePercent"], reverse=True)
    return items[:30]


async def get_sectors(cache: CacheClient | None = None) -> dict[str, Any]:
    """Return industry sector performance as ``{as_of, as_of_quality, items}``.

    Cache key carries the resolved day (``market:sectors:{as_of}``).
    """
    async with async_session_factory() as db:
        md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
        cache_key = _day_cache_key("market:sectors", md)
        if md is not None and cache:
            cached = await cache.get(cache_key)
            if isinstance(cached, dict):  # bare-list payloads from before this change = miss
                return cast(dict[str, Any], cached)
        rows = await load_day_rows(db, md.day, cache=cache) if md is not None else []

    payload = _envelope(md, _sector_items(rows))
    if cache and md is not None:
        await cache.set(cache_key, payload, _MARKET_CACHE_TTL)
    return payload


def _capital_flow_items(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Top-10 CSRC industries by turnover, split into inflow / outflow (``amount``).

    Direction now rides the stored ``pct_chg`` instead of ``close >= COALESCE(prev,
    close)``: ``pct_chg >= 0`` → inflow, ``pct_chg < 0`` → outflow, and a **missing**
    ``pct_chg`` → inflow — which is what the retired ``COALESCE(prev.close, dq.close)``
    produced (no previous close made the comparison trivially true). Ranking is by the
    group's unrounded total ``amount``; the payload then converts 千元 → 亿元 (``/1e5``)
    and negates the outflow side, as before.
    """
    scored: list[tuple[float, dict[str, Any]]] = []
    for name, group in group_by(rows, "csrc_desc").items():
        inflow = 0.0
        outflow = 0.0
        for row in group:
            amount = row.get("amount") or 0.0
            chg = row.get("pct_chg")
            if chg is not None and chg < 0:
                outflow += amount
            else:  # pct_chg >= 0 or missing → inflow (legacy COALESCE behaviour)
                inflow += amount
        scored.append(
            (
                inflow + outflow,
                {
                    "name": name,
                    "inflow": round(inflow / 1e5, 2),
                    "outflow": round(-outflow / 1e5, 2),
                },
            )
        )
    scored.sort(key=lambda entry: entry[0], reverse=True)
    return [item for _total, item in scored[:10]]


async def get_capital_flow(cache: CacheClient | None = None) -> dict[str, Any]:
    """Return sector-level turnover distribution as an ``as_of`` envelope.

    Cache key carries the resolved day (``market:capital-flow:{as_of}``).
    """
    async with async_session_factory() as db:
        md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
        cache_key = _day_cache_key("market:capital-flow", md)
        if md is not None and cache:
            cached = await cache.get(cache_key)
            if isinstance(cached, dict):  # bare-list payloads from before this change = miss
                return cast(dict[str, Any], cached)
        rows = await load_day_rows(db, md.day, cache=cache) if md is not None else []

    payload = _envelope(md, _capital_flow_items(rows))
    if cache and md is not None:
        await cache.set(cache_key, payload, _MARKET_CACHE_TTL)
    return payload


def _hot_board_items(
    rows: list[dict[str, Any]], category: HotBoardCategory
) -> list[dict[str, Any]]:
    """Top-10 boards for one category, sorted by the display-rounded average change.

    Phase 3 will swap the *data source* (East Money boards) behind these same item
    keys; this task only moves the rows onto the shared snapshot. Counts come from
    :func:`summarize_group`, so ``up + flat + down == total`` holds — the retired SQL
    could leave suspended/no-quote rows out of all three counts.
    """
    key = "csrc_desc" if category == "industry" else "province"  # concept never gets here
    items: list[dict[str, Any]] = []
    for name, group in group_by(rows, key).items():
        stats = summarize_group(group)
        items.append(
            {
                "id": f"{category}-{name}",
                "name": name,
                "code": "",
                "changePercent": round(stats["avg_chg"], 2),
                "upCount": stats["up_count"],
                "flatCount": stats["flat_count"],
                "downCount": stats["down_count"],
                "leaders": [],
            }
        )
    items.sort(key=lambda item: item["changePercent"], reverse=True)
    return items[:10]


async def get_hot_boards(
    category: HotBoardCategory,
    cache: CacheClient | None = None,
) -> dict[str, Any]:
    """Return hot boards for the given category as an ``as_of`` envelope.

    ``concept`` has no data source yet, so it returns an empty envelope without
    probing the DB (``as_of=None`` / ``partial``).
    """
    if category == "concept":
        return _envelope(None, [])

    async with async_session_factory() as db:
        md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
        cache_key = _day_cache_key(f"market:hot-boards:{category}", md)
        if md is not None and cache:
            cached = await cache.get(cache_key)
            if isinstance(cached, dict):  # bare-list payloads from before this change = miss
                return cast(dict[str, Any], cached)
        rows = await load_day_rows(db, md.day, cache=cache) if md is not None else []

    payload = _envelope(md, _hot_board_items(rows, category))
    if cache and md is not None:
        await cache.set(cache_key, payload, _MARKET_CACHE_TTL)
    return payload


# ---------------------------------------------------------------------------
# Public rankings — indexed top-N, enriched after the sort is locked
# ---------------------------------------------------------------------------

# Ruling O: only these five types exist; the sort column/direction come from this
# file-local whitelist and are f-string-interpolated into the SQL. No user input
# ever reaches the SQL string (unknown types raise before any query runs).
_RANKING_ORDER: dict[str, tuple[str, str]] = {
    "gainers": ("pct_chg", "DESC"),
    "losers": ("pct_chg", "ASC"),
    "amount": ("amount", "DESC"),
    "volume": ("volume", "DESC"),
    "turnover_rate": ("turnover_rate", "DESC"),
}


def _build_quote_rank_sql(order_col: str, order_dir: str) -> TextClause:
    """Build the top-N query for a ``daily_quotes``-backed ranking type.

    Ruling P: the ``pct_chg IS NOT NULL`` filter must stay — Postgres sorts
    DESC as NULLS FIRST, so without it the 涨幅榜 leads with unrankable rows.
    The outer ``ORDER BY`` re-imposes the order after the enrichment JOIN so the
    join can never reshuffle the locked top-N. Both ORDER BYs carry a
    ``stock_id ASC`` tiebreak: without it, rows with equal sort values (e.g. all
    zero-volume 成交额 rows, many 涨停 ties) have no stable order and the top-N
    selection / response order can shuffle between identical calls.
    """
    return text(f"""
        WITH topn AS (
            SELECT q.stock_id, q.close, q.pct_chg, q.amount, q.volume
            FROM daily_quotes q
            WHERE q.trade_date = :as_of AND q.pct_chg IS NOT NULL
            ORDER BY q.{order_col} {order_dir}, q.stock_id ASC
            LIMIT :limit
        )
        SELECT s.symbol, s.name, s.exchange, t.close, t.pct_chg, t.amount, t.volume,
               b.turnover_rate, b.total_mv
        FROM topn t
        JOIN stocks s ON s.id = t.stock_id
        LEFT JOIN daily_basic_indicators b
               ON b.stock_id = t.stock_id AND b.trade_date = :as_of
        ORDER BY t.{order_col} {order_dir}, t.stock_id ASC
    """)


_QUOTE_RANK_SQL: dict[str, TextClause] = {
    rank_type: _build_quote_rank_sql(order_col, order_dir)
    for rank_type, (order_col, order_dir) in _RANKING_ORDER.items()
    if rank_type != "turnover_rate"
}

# turnover_rate lives in daily_basic_indicators, so it needs its own top-N CTE.
# ``stock_id ASC`` tiebreak mirrors _build_quote_rank_sql — equal turnover_rate
# rows must not shuffle between identical calls.
_TURNOVER_RANK_SQL = text("""
    WITH topn AS (
        SELECT b.stock_id, b.turnover_rate, b.total_mv
        FROM daily_basic_indicators b
        WHERE b.trade_date = :as_of AND b.turnover_rate IS NOT NULL
        ORDER BY b.turnover_rate DESC, b.stock_id ASC
        LIMIT :limit
    )
    SELECT s.symbol, s.name, s.exchange, q.close, q.pct_chg, q.amount, q.volume,
           t.turnover_rate, t.total_mv
    FROM topn t
    JOIN stocks s ON s.id = t.stock_id
    LEFT JOIN daily_quotes q ON q.stock_id = t.stock_id AND q.trade_date = :as_of
    ORDER BY t.turnover_rate DESC, t.stock_id ASC
""")


async def _ranking_rows(
    db: AsyncSession, rank_type: str, day: date, limit: int
) -> list[dict[str, Any]]:
    """Top-N ranking rows for one day (seam: unit tests monkeypatch this)."""
    stmt = _TURNOVER_RANK_SQL if rank_type == "turnover_rate" else _QUOTE_RANK_SQL[rank_type]
    rows = (await db.execute(stmt, {"as_of": day, "limit": limit})).mappings().all()
    return [dict(row) for row in rows]


async def get_rankings(
    db: AsyncSession,
    cache: CacheClient | None,
    rank_type: str,
    limit: int,
) -> RankingResponseOut:
    """Return the public top-N ranking for ``rank_type`` as of the resolved day.

    Cache-first (ruling S) mirroring ``get_distribution``; the key carries the
    resolved day (``market:rankings:{type}:{limit}:{as_of}``). ``as_of`` and
    ``as_of_quality`` come from the completeness predicate (Task 1) rather than a
    raw ``max(trade_date)`` — a dirty latest day must not blank the ranking.
    """
    if rank_type not in _RANKING_ORDER:
        raise ValueError(f"unknown ranking type: {rank_type}")

    # Resolve the day *before* the cache read: the key carries the resolved ``as_of``
    # (``market:rankings:{type}:{limit}:{as_of}``), so a repaired/rolled-over day can
    # never be served from the previous day's payload. The resolver itself is Redis
    # cached (60s), so this costs nothing on the warm path.
    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    if md is None:
        # Public homepage block: an empty daily_quotes must degrade to a coherent
        # empty payload (as get_distribution falls back), never a 500. Not cached —
        # so the block recovers on the first ingest after the DB is populated.
        return RankingResponseOut(
            # 与同函数主体一致用上海判据日：宿主本地 date.today() 在翻日窗口会错标标签。
            as_of=last_weekday(_today_sh()),
            as_of_quality="partial",
            is_latest_trading_day=False,
            type=cast(RankingType, rank_type),
            items=[],
        )

    cache_key = f"market:rankings:{rank_type}:{limit}:{md.day.isoformat()}"
    if cache:
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):  # non-dict = foreign/legacy shape, treat as a miss
            # Payloads written before ``as_of_quality`` existed lack the key, and the
            # schema default is the optimistic "complete" — that would label a stale
            # payload as a fresh complete day for up to _MARKET_CACHE_TTL. Absent key
            # means the quality is unknown -> "partial" (never claim completeness).
            return RankingResponseOut.model_validate(
                {**cached, "as_of_quality": cached.get("as_of_quality", "partial")}
            )

    rows = await _ranking_rows(db, rank_type, md.day, limit)
    out = RankingResponseOut(
        as_of=md.day,
        as_of_quality=md.quality,
        as_of_reason=md.reason,
        # "最新交易日"判据：展示的 md.day 是否**等于**当前（上海时区）最近预期交易日。
        # 与 as_of_quality 相互独立、不可互推：quality 讲"表内最新行是否被跳过"
        # （回落/未来脏行场景），布尔只讲"展示的这一天是不是预期交易日"。二者可以
        # 同时成立——例如候选 09-18 是未来脏行、resolver 回落到 09-17，而今天正是
        # 09-17：展示日就是最新交易日（True），同时最新行被跳过（quality="fallback"）。
        # 用 quality 去否掉布尔会把"正在展示的那一天"误标成"不是最新交易日"。
        is_latest_trading_day=md.day == last_weekday(_today_sh()),
        type=cast(RankingType, rank_type),
        items=[RankingItemOut(**row) for row in rows],
    )
    if cache:
        await cache.set(cache_key, out.model_dump(mode="json"), _MARKET_CACHE_TTL)
    return out


# ---------------------------------------------------------------------------
# SW L1 industry performance (Task 3.1) — two-hop parent_code rollup
# ---------------------------------------------------------------------------

# Ruling U: roll L3 members up to L1 via the verified parent_code chain
# (``sw_industry_classes`` levels L1=31 / L2=134 / L3=346; the two-hop chain
# resolves for all L3 classes). Join keys: ``sw_industry_members.symbol`` ->
# ``stocks.symbol`` (95.4% match, the correct key). Members are joined to
# ``daily_quotes`` on the latest trade date so ``member_count`` / ``up_count`` /
# ``down_count`` count only stocks that actually have a quote that day, and
# ``avg_pct_chg`` is a true average over those stocks. ``pct_chg IS NOT NULL``
# keeps suspended/no-quote names out of the denominator.
_SW_PERF_SQL = text("""
    WITH l1_members AS (
        SELECT c1.industry_code AS code, c1.industry_name AS name, m.symbol
        FROM sw_industry_members m
        JOIN sw_industry_classes c3
          ON c3.industry_code = m.industry_code AND c3.level = 3
        JOIN sw_industry_classes c2 ON c2.industry_code = c3.parent_code
        JOIN sw_industry_classes c1 ON c1.industry_code = c2.parent_code
    )
    SELECT lm.code, lm.name,
           count(q.stock_id) AS member_count,
           avg(q.pct_chg) AS avg_pct_chg,
           sum(q.amount) AS total_amount,
           count(*) FILTER (WHERE q.pct_chg > 0) AS up_count,
           count(*) FILTER (WHERE q.pct_chg < 0) AS down_count
    FROM l1_members lm
    JOIN stocks s ON s.symbol = lm.symbol
    JOIN daily_quotes q ON q.stock_id = s.id AND q.trade_date = :as_of
    WHERE q.pct_chg IS NOT NULL
    GROUP BY lm.code, lm.name
    ORDER BY avg_pct_chg DESC
""")


async def _sw_performance_rows(db: AsyncSession, day: date) -> list[dict[str, Any]]:
    """SW L1 rollup rows for one day (seam: unit tests monkeypatch this)."""
    rows = (await db.execute(_SW_PERF_SQL, {"as_of": day})).mappings().all()
    return [dict(row) for row in rows]


async def get_sw_industry_performance(
    db: AsyncSession,
    cache: CacheClient | None,
    limit: int = 31,
) -> SwPerformanceResponseOut:
    """Return Shenwan L1 industry performance as of the resolved day.

    Cache-first (ruling U) mirroring ``get_rankings``; the full L1 set is cached
    under one **day-scoped** key (``market:sw-performance:{as_of}``) and ``limit`` is
    applied on read (the SQL returns all L1 rows). ``as_of_quality`` comes from the
    completeness predicate (Task 1).
    """
    # Same ordering rule as ``get_rankings``: resolve first, because the key carries
    # the resolved day (``market:sw-performance:{as_of}``).
    md = await market_day_service.resolve_latest_complete_day(db, cache=cache)
    if md is None:
        # Anonymous homepage block: an empty daily_quotes degrades to an empty
        # payload (never 500) and is not cached, so it recovers after the first ingest.
        return SwPerformanceResponseOut(
            as_of=last_weekday(date.today()), as_of_quality="partial", items=[]
        )

    cache_key = f"market:sw-performance:{md.day.isoformat()}"
    if cache:
        cached = await cache.get(cache_key)
        if isinstance(cached, dict):  # non-dict = foreign/legacy shape, treat as a miss
            # Same rule as get_rankings: a pre-deploy payload has no ``as_of_quality``
            # and the schema default ("complete") must not be mistaken for real evidence.
            out = SwPerformanceResponseOut.model_validate(
                {**cached, "as_of_quality": cached.get("as_of_quality", "partial")}
            )
            return out.model_copy(update={"items": out.items[:limit]})

    rows = await _sw_performance_rows(db, md.day)
    out = SwPerformanceResponseOut(
        as_of=md.day,
        as_of_quality=md.quality,
        as_of_reason=md.reason,
        items=[SwPerformanceItemOut(**row) for row in rows],
    )
    if cache:
        await cache.set(cache_key, out.model_dump(mode="json"), _MARKET_CACHE_TTL)
    return out.model_copy(update={"items": out.items[:limit]})


# ---------------------------------------------------------------------------
# Index K-line from DB
# ---------------------------------------------------------------------------


async def get_index_kline(
    ts_code: str,
    start_date: date | None = None,
    end_date: date | None = None,
    cache: CacheClient | None = None,
) -> list[dict[str, Any]]:
    """Return index daily K-line data from index_dailies table."""
    start_str = str(start_date) if start_date else "all"
    end_str = str(end_date) if end_date else "all"
    cache_key = f"market:index-kline:{ts_code}:{start_str}:{end_str}"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cast(list[dict[str, Any]], cached)

    from app.repositories import index_repo  # noqa: PLC0415

    async with async_session_factory() as db:
        rows = await index_repo.get_kline(db, ts_code, start_date, end_date)

    data = [
        {
            "trade_date": str(r.trade_date),
            "open": float(r.open) if r.open is not None else None,
            "high": float(r.high) if r.high is not None else None,
            "low": float(r.low) if r.low is not None else None,
            "close": float(r.close),
            "pre_close": float(r.pre_close) if r.pre_close is not None else None,
            "volume": float(r.volume) if r.volume is not None else None,
            "amount": float(r.amount) if r.amount is not None else None,
        }
        for r in rows
    ]
    if cache and data:
        await cache.set(cache_key, data, _MARKET_CACHE_TTL)
    return data


# ---------------------------------------------------------------------------
# SW Industry tree — DB-backed, built from local XLS imports
# ---------------------------------------------------------------------------


async def get_sw_industry_tree(cache: CacheClient | None = None) -> list[dict]:
    """Build the three-level SW industry tree from DB with stock counts.

    Returns a nested structure:
    [{ code, name, stockCount, children: [
        { code, name, stockCount, children: [{ code, name, stockCount, symbols }] }
    ] }]
    """
    cache_key = "market:sw-tree"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cast(list[dict], cached)

    from app.models.sw_industry import (  # noqa: PLC0415
        StockCustomSwTag,
        SwIndustryClass,
        SwIndustryMember,
    )

    async with async_session_factory() as db:
        # Fetch all classification nodes
        classes_result = await db.execute(
            select(SwIndustryClass).order_by(SwIndustryClass.industry_code)
        )
        all_classes = classes_result.scalars().all()

        # Collect L3 symbols from both official members and custom L3 tags
        symbols_by_code: dict[str, set[str]] = {}
        official_l3_symbols = await db.execute(
            select(SwIndustryMember.industry_code, SwIndustryMember.symbol).join(
                Stock, Stock.symbol == SwIndustryMember.symbol
            )
        )
        for row in official_l3_symbols:
            symbols_by_code.setdefault(row.industry_code, set()).add(row.symbol)

        custom_l3_symbols = await db.execute(
            select(StockCustomSwTag.industry_code, StockCustomSwTag.symbol)
            .join(
                SwIndustryClass,
                SwIndustryClass.industry_code == StockCustomSwTag.industry_code,
            )
            .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
            .where(SwIndustryClass.level == 3)
        )
        for row in custom_l3_symbols:
            symbols_by_code.setdefault(row.industry_code, set()).add(row.symbol)

        # Keep L2 custom symbols separately: they contribute to L2/L1 totals
        custom_l2_symbols_by_code: dict[str, set[str]] = {}
        custom_l2_symbols = await db.execute(
            select(StockCustomSwTag.industry_code, StockCustomSwTag.symbol)
            .join(
                SwIndustryClass,
                SwIndustryClass.industry_code == StockCustomSwTag.industry_code,
            )
            .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
            .where(SwIndustryClass.level == 2)
        )
        for row in custom_l2_symbols:
            custom_l2_symbols_by_code.setdefault(row.industry_code, set()).add(row.symbol)

        # Symbols that do not map to any valid L3 industry should go to "其他"
        categorized_symbols_subq = union(
            select(SwIndustryMember.symbol)
            .join(
                SwIndustryClass,
                SwIndustryClass.industry_code == SwIndustryMember.industry_code,
            )
            .where(SwIndustryClass.level == 3),
            select(StockCustomSwTag.symbol)
            .join(
                SwIndustryClass,
                SwIndustryClass.industry_code == StockCustomSwTag.industry_code,
            )
            .where(SwIndustryClass.level.in_([2, 3])),
        ).subquery()
        effective_industry = func.coalesce(Stock.industry, Stock.csrc_desc)
        uncategorized_result = await db.execute(
            select(Stock.symbol, effective_industry.label("eff_industry"))
            .outerjoin(
                categorized_symbols_subq,
                categorized_symbols_subq.c.symbol == Stock.symbol,
            )
            .where(categorized_symbols_subq.c.symbol.is_(None))
            .order_by(Stock.exchange, Stock.symbol)
        )
        uncategorized_rows = list(uncategorized_result.all())
        uncategorized_symbols = [r.symbol for r in uncategorized_rows]

        # Group uncategorized symbols by effective industry (industry or csrc_desc)
        industry_groups: dict[str, list[str]] = {}
        for row in uncategorized_rows:
            key = row.eff_industry or _SW_OTHER_UNKNOWN_INDUSTRY
            industry_groups.setdefault(key, []).append(row.symbol)

    # Build tree from flat list
    l1_nodes: dict[str, dict] = {}
    l2_nodes: dict[str, dict] = {}
    l3_nodes: dict[str, dict] = {}

    l2_symbol_sets: dict[str, set[str]] = {}
    l1_symbol_sets: dict[str, set[str]] = {}

    for cls in all_classes:
        if cls.level == 1:
            l1_nodes[cls.industry_code] = {
                "code": cls.industry_code,
                "name": cls.industry_name,
                "stockCount": 0,
                "children": [],
            }
            l1_symbol_sets[cls.industry_code] = set()
        elif cls.level == 2:
            l2_nodes[cls.industry_code] = {
                "code": cls.industry_code,
                "name": cls.industry_name,
                "stockCount": 0,
                "parent_code": cls.parent_code,
                "children": [],
            }
            l2_symbol_sets[cls.industry_code] = set(
                custom_l2_symbols_by_code.get(cls.industry_code, set())
            )
        elif cls.level == 3:
            l3_symbols = sorted(symbols_by_code.get(cls.industry_code, set()))
            l3_nodes[cls.industry_code] = {
                "code": cls.industry_code,
                "name": cls.industry_name,
                "stockCount": len(l3_symbols),
                "parent_code": cls.parent_code,
                "symbols": l3_symbols,
            }

    # Attach L3 to L2
    for l3 in l3_nodes.values():
        parent = l3.get("parent_code")
        if parent and parent in l2_nodes:
            l2_nodes[parent]["children"].append(l3)
            l2_symbol_sets[parent].update(l3["symbols"])

    # Attach L2 to L1
    for l2 in l2_nodes.values():
        parent = l2.get("parent_code")
        if parent and parent in l1_nodes:
            l1_node = l1_nodes[parent]
            l2["stockCount"] = len(l2_symbol_sets.get(l2["code"], set()))
            # Remove parent_code from output
            l2_out = {k: v for k, v in l2.items() if k != "parent_code"}
            l1_node["children"].append(l2_out)
            l1_symbol_sets[parent].update(l2_symbol_sets.get(l2["code"], set()))

    # Clean parent_code from L3 output
    for l2 in l2_nodes.values():
        for child in l2["children"]:
            child.pop("parent_code", None)

    for l1_code, l1_node in l1_nodes.items():
        l1_node["stockCount"] = len(l1_symbol_sets.get(l1_code, set()))

    tree = list(l1_nodes.values())
    if uncategorized_symbols:
        other_children = [
            {
                "code": f"OTHER_{name}",
                "name": name,
                "stockCount": len(syms),
                "children": [],
            }
            for name, syms in sorted(industry_groups.items())
        ]
        tree.append(
            {
                "code": _SW_OTHER_LEVEL1_CODE,
                "name": _SW_OTHER_LEVEL1_NAME,
                "stockCount": len(uncategorized_symbols),
                "children": other_children,
            }
        )

    if cache and tree:
        await cache.set(cache_key, tree, _MARKET_CACHE_TTL)
    return tree


# ---------------------------------------------------------------------------
# SW tree navigation helpers — DB-backed
# ---------------------------------------------------------------------------


async def get_sw_level1(level1_code: str) -> dict | None:
    """Check if a level-1 industry code exists."""
    from app.models.sw_industry import SwIndustryClass  # noqa: PLC0415

    if level1_code == _SW_OTHER_LEVEL1_CODE:
        return {"code": _SW_OTHER_LEVEL1_CODE, "name": _SW_OTHER_LEVEL1_NAME}

    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SwIndustryClass).where(
                    SwIndustryClass.industry_code == level1_code,
                    SwIndustryClass.level == 1,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {"code": row.industry_code, "name": row.industry_name}


async def get_sw_level2(level1_code: str, level2_code: str) -> dict | None:
    """Check if a level-2 industry code exists under the given level-1."""
    from app.models.sw_industry import SwIndustryClass  # noqa: PLC0415

    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SwIndustryClass).where(
                    SwIndustryClass.industry_code == level2_code,
                    SwIndustryClass.level == 2,
                    SwIndustryClass.parent_code == level1_code,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {"code": row.industry_code, "name": row.industry_name}


async def get_sw_level3(level1_code: str, level2_code: str, level3_code: str) -> dict | None:
    """Check if a level-3 industry code exists under the given level-2."""
    from app.models.sw_industry import SwIndustryClass  # noqa: PLC0415

    async with async_session_factory() as db:
        row = (
            await db.execute(
                select(SwIndustryClass).where(
                    SwIndustryClass.industry_code == level3_code,
                    SwIndustryClass.level == 3,
                    SwIndustryClass.parent_code == level2_code,
                )
            )
        ).scalar_one_or_none()
    if row is None:
        return None
    return {"code": row.industry_code, "name": row.industry_name}


# ---------------------------------------------------------------------------
# Per-stock SW chain (L1→L2→L3) — for stock-detail breadcrumbs
# ---------------------------------------------------------------------------

# Resolve the stock's own L3 membership, then walk parent_code up to L1 in one
# round trip. Members map to L3 codes only; a stock may rarely map to several
# L3s — we deterministically keep the smallest industry_code.
_SW_CHAIN_SQL = """
WITH RECURSIVE chain AS (
    SELECT * FROM (
        SELECT c.industry_code, c.level, c.industry_name, c.parent_code
        FROM sw_industry_members m
        JOIN sw_industry_classes c ON c.industry_code = m.industry_code
        WHERE m.symbol = :symbol AND c.level = 3
        ORDER BY c.industry_code
        LIMIT 1
    ) anchor
  UNION ALL
    SELECT c.industry_code, c.level, c.industry_name, c.parent_code
    FROM sw_industry_classes c
    JOIN chain ch ON c.industry_code = ch.parent_code
)
SELECT industry_code, level, industry_name FROM chain
"""


def assemble_sw_chain(rows: list[dict]) -> list[dict]:
    """Pure: order recursive-CTE rows into an L1→L2→L3 chain payload.

    Rows may arrive in any order; only levels actually found are emitted, so a
    partially-resolved tree degrades gracefully instead of erroring.
    """
    by_level = {row["level"]: row for row in rows}
    return [
        {
            "level": level,
            "code": by_level[level]["industry_code"],
            "name": by_level[level]["industry_name"],
        }
        for level in (1, 2, 3)
        if level in by_level
    ]


async def get_sw_chain_by_symbol(db: AsyncSession, symbol: str) -> list[dict]:
    """Resolve a stock's own SW industry chain from its official L3 membership.

    Entry-point independent (derived from sw_industry_members only); returns an
    empty list when the stock has no official SW mapping.
    """
    from sqlalchemy import text  # noqa: PLC0415

    result = await db.execute(text(_SW_CHAIN_SQL), {"symbol": symbol})
    return assemble_sw_chain([dict(r) for r in result.mappings().all()])


async def list_symbols_by_level1(level1_code: str) -> list[str]:
    """Get all member symbols under a level-1 industry."""
    from app.models.sw_industry import (  # noqa: PLC0415
        StockCustomSwTag,
        SwIndustryClass,
        SwIndustryMember,
    )

    async with async_session_factory() as db:
        if level1_code == _SW_OTHER_LEVEL1_CODE:
            categorized_symbols_subq = (
                select(SwIndustryMember.symbol)
                .join(
                    SwIndustryClass,
                    SwIndustryClass.industry_code == SwIndustryMember.industry_code,
                )
                .where(SwIndustryClass.level == 3)
                .distinct()
                .subquery()
            )
            uncategorized = (
                (
                    await db.execute(
                        select(Stock.symbol)
                        .outerjoin(
                            categorized_symbols_subq,
                            categorized_symbols_subq.c.symbol == Stock.symbol,
                        )
                        .where(categorized_symbols_subq.c.symbol.is_(None))
                        .order_by(Stock.exchange, Stock.symbol)
                    )
                )
                .scalars()
                .all()
            )
            return list(uncategorized)

        # L1 -> L2 codes -> L3 codes -> members
        l2_codes = (
            (
                await db.execute(
                    select(SwIndustryClass.industry_code).where(
                        SwIndustryClass.parent_code == level1_code,
                        SwIndustryClass.level == 2,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not l2_codes:
            return []
        l3_codes = (
            (
                await db.execute(
                    select(SwIndustryClass.industry_code).where(
                        SwIndustryClass.parent_code.in_(l2_codes),
                        SwIndustryClass.level == 3,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not l3_codes:
            return []
        official_symbols = (
            (
                await db.execute(
                    select(SwIndustryMember.symbol)
                    .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                    .where(SwIndustryMember.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        custom_l2_symbols = (
            (
                await db.execute(
                    select(StockCustomSwTag.symbol)
                    .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                    .where(StockCustomSwTag.industry_code.in_(l2_codes))
                )
            )
            .scalars()
            .all()
        )
        custom_l3_symbols = (
            (
                await db.execute(
                    select(StockCustomSwTag.symbol)
                    .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                    .where(StockCustomSwTag.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        symbol_set = set(official_symbols) | set(custom_l2_symbols) | set(custom_l3_symbols)
        ordered = (
            (
                await db.execute(
                    select(Stock.symbol)
                    .where(Stock.symbol.in_(symbol_set))
                    .order_by(Stock.exchange, Stock.symbol)
                )
            )
            .scalars()
            .all()
        )
    return list(ordered)


async def list_symbols_by_level2(level1_code: str, level2_code: str) -> list[str]:
    """Get all member symbols under a level-2 industry."""
    from app.models.sw_industry import (  # noqa: PLC0415
        StockCustomSwTag,
        SwIndustryClass,
        SwIndustryMember,
    )

    async with async_session_factory() as db:
        l3_codes = (
            (
                await db.execute(
                    select(SwIndustryClass.industry_code).where(
                        SwIndustryClass.parent_code == level2_code,
                        SwIndustryClass.level == 3,
                    )
                )
            )
            .scalars()
            .all()
        )
        if not l3_codes:
            return []
        official_symbols = (
            (
                await db.execute(
                    select(SwIndustryMember.symbol)
                    .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                    .where(SwIndustryMember.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        custom_l2_symbols = (
            (
                await db.execute(
                    select(StockCustomSwTag.symbol)
                    .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                    .where(StockCustomSwTag.industry_code == level2_code)
                )
            )
            .scalars()
            .all()
        )
        custom_l3_symbols = (
            (
                await db.execute(
                    select(StockCustomSwTag.symbol)
                    .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                    .where(StockCustomSwTag.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        symbol_set = set(official_symbols) | set(custom_l2_symbols) | set(custom_l3_symbols)
        ordered = (
            (
                await db.execute(
                    select(Stock.symbol)
                    .where(Stock.symbol.in_(symbol_set))
                    .order_by(Stock.exchange, Stock.symbol)
                )
            )
            .scalars()
            .all()
        )
    return list(ordered)


async def list_symbols_by_industry_codes(l3_codes: list[str]) -> list[str]:
    """Get member symbols under any of the given level-3 industry codes.

    官方申万成分 + 自定义标签并集，仅保留 stocks 表内在市标的（按交易所+代码排序）。
    行业工作台 companies 端点按 registry sw_l3_codes 复用本查询。
    """
    from app.models.sw_industry import StockCustomSwTag, SwIndustryMember  # noqa: PLC0415

    if not l3_codes:
        return []

    async with async_session_factory() as db:
        official_symbols = (
            (
                await db.execute(
                    select(SwIndustryMember.symbol)
                    .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                    .where(SwIndustryMember.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        custom_symbols = (
            (
                await db.execute(
                    select(StockCustomSwTag.symbol)
                    .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                    .where(StockCustomSwTag.industry_code.in_(l3_codes))
                )
            )
            .scalars()
            .all()
        )
        symbol_set = set(official_symbols) | set(custom_symbols)
        ordered = (
            (
                await db.execute(
                    select(Stock.symbol)
                    .where(Stock.symbol.in_(symbol_set))
                    .order_by(Stock.exchange, Stock.symbol)
                )
            )
            .scalars()
            .all()
        )
    return list(ordered)


async def list_symbols_by_level3(level1_code: str, level2_code: str, level3_code: str) -> list[str]:
    """Get all member symbols under a level-3 industry."""
    return await list_symbols_by_industry_codes([level3_code])


def _uncategorized_symbols_subquery() -> Subquery:
    """Shared subquery for uncategorized (OTHER) symbols."""
    from app.models.sw_industry import (  # noqa: PLC0415
        StockCustomSwTag,
        SwIndustryClass,
        SwIndustryMember,
    )

    return union(
        select(SwIndustryMember.symbol)
        .join(
            SwIndustryClass,
            SwIndustryClass.industry_code == SwIndustryMember.industry_code,
        )
        .where(SwIndustryClass.level == 3),
        select(StockCustomSwTag.symbol)
        .join(
            SwIndustryClass,
            SwIndustryClass.industry_code == StockCustomSwTag.industry_code,
        )
        .where(SwIndustryClass.level.in_([2, 3])),
    ).subquery()


def _effective_industry() -> Any:
    """COALESCE(industry, csrc_desc) — the best available industry value."""
    return func.coalesce(Stock.industry, Stock.csrc_desc)


async def get_sw_other_level2(industry_name: str) -> dict | None:
    """Check if an OTHER sub-group exists by industry name."""
    categorized_subq = _uncategorized_symbols_subquery()
    eff = _effective_industry()
    async with async_session_factory() as db:
        stmt = (
            select(func.count())
            .select_from(Stock)
            .outerjoin(categorized_subq, categorized_subq.c.symbol == Stock.symbol)
            .where(categorized_subq.c.symbol.is_(None))
        )
        if industry_name == _SW_OTHER_UNKNOWN_INDUSTRY:
            stmt = stmt.where(eff.is_(None))
        else:
            stmt = stmt.where(eff == industry_name)

        cnt = (await db.execute(stmt)).scalar_one()
    if cnt == 0:
        return None
    return {"code": f"OTHER_{industry_name}", "name": industry_name}


async def list_symbols_by_other_level2(industry_name: str) -> list[str]:
    """Get symbols in the OTHER L1 filtered by effective industry."""
    categorized_subq = _uncategorized_symbols_subquery()
    eff = _effective_industry()

    async with async_session_factory() as db:
        stmt = (
            select(Stock.symbol)
            .outerjoin(categorized_subq, categorized_subq.c.symbol == Stock.symbol)
            .where(categorized_subq.c.symbol.is_(None))
        )
        if industry_name == _SW_OTHER_UNKNOWN_INDUSTRY:
            stmt = stmt.where(eff.is_(None))
        else:
            stmt = stmt.where(eff == industry_name)

        symbols = (await db.execute(stmt.order_by(Stock.symbol))).scalars().all()
    return list(symbols)


async def list_stocks_by_symbols(db: AsyncSession, symbols: list[str]) -> list[StockOut]:
    if not symbols:
        return []
    rows = (await db.execute(select(Stock).where(Stock.symbol.in_(symbols)))).scalars().all()
    if not rows:
        return []
    stock_by_symbol: dict[str, StockOut] = {}
    for row in rows:
        stock_by_symbol.setdefault(row.symbol, StockOut.model_validate(row))
    return [stock_by_symbol[symbol] for symbol in symbols if symbol in stock_by_symbol]


# ---------------------------------------------------------------------------
# Enriched stock listing — joins latest quote + daily_basic in one round trip
# ---------------------------------------------------------------------------

from app.schemas.stock import StockEnrichedOut  # noqa: E402

_GET_ENRICHED_SQL = """
SELECT
    s.id, s.exchange, s.symbol, s.name, s.area, s.industry,
    s.full_name, s.enname, s.cnspell, s.market, s.curr_type,
    s.list_status, s.list_date, s.delist_date, s.is_hs,
    s.act_name, s.act_ent_type, s.category, s.csrc_code, s.csrc_desc,
    s.province, s.status, s.detail, s.asof,
    q.close     AS latest_price,
    q.open      AS "open",
    q.high      AS high,
    q.low       AS low,
    q.volume    AS volume,
    q.amount    AS amount,
    q.trade_date AS latest_quote_date,
    q2.close    AS prev_close,
    d.pe_ttm    AS pe_ttm,
    d.pb        AS pb,
    d.total_mv  AS total_mv,
    d.circ_mv   AS circ_mv,
    d.turnover_rate AS turnover_rate
FROM stocks s
LEFT JOIN LATERAL (
    SELECT open, high, low, close, volume, amount, trade_date
    FROM daily_quotes
    WHERE stock_id = s.id
    ORDER BY trade_date DESC
    LIMIT 1
) q ON true
LEFT JOIN LATERAL (
    SELECT close
    FROM daily_quotes
    WHERE stock_id = s.id AND trade_date < q.trade_date
    ORDER BY trade_date DESC
    LIMIT 1
) q2 ON true
LEFT JOIN LATERAL (
    SELECT pe_ttm, pb, total_mv, circ_mv, turnover_rate
    FROM daily_basic_indicators
    WHERE stock_id = s.id
    ORDER BY trade_date DESC
    LIMIT 1
) d ON true
WHERE s.symbol = ANY(:symbols)
ORDER BY s.symbol
"""


async def get_stocks_enriched_by_symbols(
    db: AsyncSession,
    symbols: list[str],
) -> list[StockEnrichedOut]:
    """Return StockEnrichedOut for each symbol, joining latest price + fundamentals."""
    if not symbols:
        return []

    from sqlalchemy import text  # noqa: PLC0415

    result = await db.execute(text(_GET_ENRICHED_SQL), {"symbols": symbols})
    rows = result.mappings().all()
    if not rows:
        return []

    stock_by_symbol: dict[str, StockEnrichedOut] = {}
    for row in rows:
        data = dict(row)
        # Compute change / change_pct from latest_price & prev_close
        lp = data.get("latest_price")
        pc = data.get("prev_close")
        if lp is not None and pc is not None and pc != 0:
            data["change"] = round(float(lp) - float(pc), 4)
            data["change_percent"] = round((float(lp) - float(pc)) / float(pc) * 100, 2)
        stock = StockEnrichedOut(**data)
        stock_by_symbol.setdefault(stock.symbol, stock)

    return [stock_by_symbol[sym] for sym in symbols if sym in stock_by_symbol]
