"""Market service: provide market dashboard data for frontend.

Data sources
------------
- **PostgreSQL**: ``index_dailies`` for main indices, ``stocks`` + ``daily_quotes``
  for aggregated stats.
- **Redis**: short-lived cache (300s) for all dashboard endpoints.
- **TuShare Pro** (via ``TuShareClient``): fallback for indices when DB is empty.
- **Static fallback**: last-resort data when both sources return empty.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.core.redis import CacheClient
from app.models.index_daily import IndexDaily
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.stock import StockOut

logger = logging.getLogger(__name__)

HotBoardCategory = Literal["industry", "concept", "region"]

_MARKET_CACHE_TTL = 300  # 5 minutes

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
# Static fallback data — returned only when both DB and TuShare are empty
# ---------------------------------------------------------------------------

_FALLBACK_DISTRIBUTION = [
    {"range": "跌停", "count": 0},
    {"range": ">-7%", "count": 0},
    {"range": "-5~-7%", "count": 0},
    {"range": "-3~-5%", "count": 0},
    {"range": "-1~-3%", "count": 0},
    {"range": "0~-1%", "count": 0},
    {"range": "0~1%", "count": 0},
    {"range": "1~3%", "count": 0},
    {"range": "3~5%", "count": 0},
    {"range": ">5%", "count": 0},
    {"range": "涨停", "count": 0},
]

# ---------------------------------------------------------------------------
# Helper: get the latest trade date from DB
# ---------------------------------------------------------------------------


async def _latest_trade_date(db: AsyncSession):
    """Return the most recent trade_date in daily_quotes, or None."""
    result = await db.execute(
        select(func.max(DailyQuote.trade_date))
    )
    return result.scalar_one_or_none()


async def _latest_trade_date_str() -> str:
    """Return latest trade date as YYYYMMDD for TuShare calls."""
    async with async_session_factory() as db:
        d = await _latest_trade_date(db)
    if d:
        return d.strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


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
            return cached

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
            results.append({
                "code": row.ts_code.split(".")[0],
                "tsCode": row.ts_code,
                "name": INDEX_NAME_MAP.get(row.ts_code, row.ts_code),
                "value": round(close, 2),
                "change": change,
                "changePercent": change_pct,
                "exchange": INDEX_EXCHANGE_MAP.get(row.ts_code, ""),
                "asof": asof,
            })
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
                if len(trade_date) == 8 else None
            )
            results.append({
                "code": idx["ts_code"].split(".")[0],
                "tsCode": idx["ts_code"],
                "name": idx["name"],
                "value": round(close, 2),
                "change": change,
                "changePercent": change_pct,
                "exchange": idx["exchange"],
                "asof": asof,
            })
        except Exception:
            logger.warning("_fetch_indices_from_tushare: failed for %s", idx["ts_code"], exc_info=True)

    return results


async def get_distribution(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """Return market-wide up/down distribution from DB daily_quotes."""
    cache_key = "market:distribution"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    async with async_session_factory() as db:
        latest = await _latest_trade_date(db)
        if not latest:
            return _FALLBACK_DISTRIBUTION

        stmt = text("""
            SELECT
                CASE
                    WHEN pct_chg <= -9.5 THEN '跌停'
                    WHEN pct_chg < -7 THEN '>-7%'
                    WHEN pct_chg < -5 THEN '-5~-7%'
                    WHEN pct_chg < -3 THEN '-3~-5%'
                    WHEN pct_chg < -1 THEN '-1~-3%'
                    WHEN pct_chg < 0 THEN '0~-1%'
                    WHEN pct_chg < 1 THEN '0~1%'
                    WHEN pct_chg < 3 THEN '1~3%'
                    WHEN pct_chg < 5 THEN '3~5%'
                    WHEN pct_chg >= 9.5 THEN '涨停'
                    ELSE '>5%'
                END AS range_label,
                COUNT(*) AS cnt
            FROM (
                SELECT
                    dq.stock_id,
                    CASE WHEN dq.close > 0 AND prev.close > 0
                         THEN ((dq.close - prev.close) / prev.close * 100)
                         ELSE 0 END AS pct_chg
                FROM daily_quotes dq
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_quotes dq2
                    WHERE dq2.stock_id = dq.stock_id
                      AND dq2.trade_date < :trade_date
                    ORDER BY dq2.trade_date DESC
                    LIMIT 1
                ) prev ON true
                WHERE dq.trade_date = :trade_date
            ) sub
            GROUP BY range_label
        """)
        result = await db.execute(stmt, {"trade_date": latest})
        db_rows = {row.range_label: row.cnt for row in result}

    if not db_rows:
        return _FALLBACK_DISTRIBUTION

    ordered_ranges = [
        "跌停", ">-7%", "-5~-7%", "-3~-5%", "-1~-3%",
        "0~-1%", "0~1%", "1~3%", "3~5%", ">5%", "涨停",
    ]
    data = [{"range": r, "count": db_rows.get(r, 0)} for r in ordered_ranges]
    if cache:
        await cache.set(cache_key, data, _MARKET_CACHE_TTL)
    return data


async def get_sectors(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """Return industry sector performance from DB."""
    cache_key = "market:sectors"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    async with async_session_factory() as db:
        latest = await _latest_trade_date(db)
        if not latest:
            return []

        stmt = text("""
            WITH latest_quotes AS (
                SELECT dq.stock_id, dq.close, dq.amount,
                       prev.close AS prev_close
                FROM daily_quotes dq
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_quotes dq2
                    WHERE dq2.stock_id = dq.stock_id
                      AND dq2.trade_date < :trade_date
                    ORDER BY dq2.trade_date DESC
                    LIMIT 1
                ) prev ON true
                WHERE dq.trade_date = :trade_date
            )
            SELECT
                s.csrc_desc AS industry,
                COUNT(*) AS stock_count,
                SUM(lq.amount) AS total_amount,
                AVG(CASE WHEN lq.prev_close > 0
                    THEN (lq.close - lq.prev_close) / lq.prev_close * 100
                    ELSE 0 END) AS avg_change_pct
            FROM stocks s
            JOIN latest_quotes lq ON lq.stock_id = s.id
            WHERE s.csrc_desc IS NOT NULL AND s.csrc_desc != ''
            GROUP BY s.csrc_desc
            ORDER BY avg_change_pct DESC
            LIMIT 30
        """)
        result = await db.execute(stmt, {"trade_date": latest})
        rows = result.fetchall()

    sectors: list[dict[str, Any]] = []
    for row in rows:
        sectors.append({
            "name": row.industry,
            "changePercent": round(float(row.avg_change_pct or 0), 2),
            "totalMarketCap": float(row.total_amount or 0) * 1000,
            "stockCount": int(row.stock_count),
            "topStocks": [],
        })
    if cache and sectors:
        await cache.set(cache_key, sectors, _MARKET_CACHE_TTL)
    return sectors


async def get_capital_flow(cache: CacheClient | None = None) -> list[dict[str, Any]]:
    """Return sector-level turnover distribution from DB."""
    cache_key = "market:capital-flow"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    async with async_session_factory() as db:
        latest = await _latest_trade_date(db)
        if not latest:
            return []

        stmt = text("""
            SELECT
                s.csrc_desc AS industry,
                SUM(CASE WHEN dq.close >= COALESCE(prev.close, dq.close)
                    THEN dq.amount ELSE 0 END) AS inflow_raw,
                SUM(CASE WHEN dq.close < COALESCE(prev.close, dq.close)
                    THEN dq.amount ELSE 0 END) AS outflow_raw
            FROM stocks s
            JOIN daily_quotes dq ON dq.stock_id = s.id AND dq.trade_date = :trade_date
            LEFT JOIN LATERAL (
                SELECT close FROM daily_quotes dq2
                WHERE dq2.stock_id = dq.stock_id
                  AND dq2.trade_date < :trade_date
                ORDER BY dq2.trade_date DESC
                LIMIT 1
            ) prev ON true
            WHERE s.csrc_desc IS NOT NULL AND s.csrc_desc != ''
            GROUP BY s.csrc_desc
            ORDER BY (SUM(dq.amount)) DESC
            LIMIT 10
        """)
        result = await db.execute(stmt, {"trade_date": latest})
        rows = result.fetchall()

    flows: list[dict[str, Any]] = []
    for row in rows:
        inflow = float(row.inflow_raw or 0) / 1e5
        outflow = float(row.outflow_raw or 0) / 1e5
        flows.append({
            "name": row.industry,
            "inflow": round(inflow, 2),
            "outflow": round(-outflow, 2),
        })
    if cache and flows:
        await cache.set(cache_key, flows, _MARKET_CACHE_TTL)
    return flows


async def get_hot_boards(
    category: HotBoardCategory,
    cache: CacheClient | None = None,
) -> list[dict[str, Any]]:
    """Return hot boards for the given category from DB."""
    if category == "concept":
        return []

    cache_key = f"market:hot-boards:{category}"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

    group_col = "csrc_desc" if category == "industry" else "province"

    async with async_session_factory() as db:
        latest = await _latest_trade_date(db)
        if not latest:
            return []

        stmt = text(f"""
            WITH latest_quotes AS (
                SELECT dq.stock_id, dq.close,
                       CASE WHEN prev.close > 0
                            THEN (dq.close - prev.close) / prev.close * 100
                            ELSE 0 END AS pct_chg
                FROM daily_quotes dq
                LEFT JOIN LATERAL (
                    SELECT close FROM daily_quotes dq2
                    WHERE dq2.stock_id = dq.stock_id
                      AND dq2.trade_date < :trade_date
                    ORDER BY dq2.trade_date DESC
                    LIMIT 1
                ) prev ON true
                WHERE dq.trade_date = :trade_date
            )
            SELECT
                s.{group_col} AS group_name,
                COUNT(*) AS total,
                SUM(CASE WHEN lq.pct_chg > 0 THEN 1 ELSE 0 END) AS up_count,
                SUM(CASE WHEN lq.pct_chg = 0 THEN 1 ELSE 0 END) AS flat_count,
                SUM(CASE WHEN lq.pct_chg < 0 THEN 1 ELSE 0 END) AS down_count,
                AVG(lq.pct_chg) AS avg_chg
            FROM stocks s
            JOIN latest_quotes lq ON lq.stock_id = s.id
            WHERE s.{group_col} IS NOT NULL AND s.{group_col} != ''
            GROUP BY s.{group_col}
            ORDER BY avg_chg DESC
            LIMIT 10
        """)  # noqa: S608
        result = await db.execute(stmt, {"trade_date": latest})
        rows = result.fetchall()

    boards: list[dict[str, Any]] = []
    for row in rows:
        boards.append({
            "id": f"{category}-{row.group_name}",
            "name": row.group_name,
            "code": "",
            "changePercent": round(float(row.avg_chg or 0), 2),
            "upCount": int(row.up_count or 0),
            "flatCount": int(row.flat_count or 0),
            "downCount": int(row.down_count or 0),
            "leaders": [],
        })
    if cache and boards:
        await cache.set(cache_key, boards, _MARKET_CACHE_TTL)
    return boards


# ---------------------------------------------------------------------------
# Index K-line from DB
# ---------------------------------------------------------------------------


async def get_index_kline(
    ts_code: str,
    start_date=None,
    end_date=None,
    cache: CacheClient | None = None,
) -> list[dict[str, Any]]:
    """Return index daily K-line data from index_dailies table."""
    start_str = str(start_date) if start_date else "all"
    end_str = str(end_date) if end_date else "all"
    cache_key = f"market:index-kline:{ts_code}:{start_str}:{end_str}"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

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
# SW Industry tree — cached in memory, loaded from TuShare on first access
# ---------------------------------------------------------------------------

_sw_tree_cache: list[dict] | None = None


async def _load_sw_tree_from_tushare() -> list[dict]:
    """Fetch Shenwan L1/L2/L3 classification from TuShare and build a tree."""
    from app.core.providers.tushare_client import get_tushare_client  # noqa: PLC0415

    client = get_tushare_client()
    levels: dict[str, list[dict]] = {}
    for lvl in ("L1", "L2", "L3"):
        df = await client.fetch_index_classify(level=lvl, src="SW")
        if not df.empty:
            levels[lvl] = df.to_dict("records")
        else:
            levels[lvl] = []

    l1_nodes: dict[str, dict] = {}
    for rec in levels.get("L1", []):
        code = str(rec.get("index_code", "")).strip()
        name = str(rec.get("industry_name", "")).strip()
        if code and name:
            l1_nodes[code] = {"code": code, "name": name, "children": []}

    l2_nodes: dict[str, dict] = {}
    for rec in levels.get("L2", []):
        code = str(rec.get("index_code", "")).strip()
        name = str(rec.get("industry_name", "")).strip()
        parent = str(rec.get("parent_code", "")).strip()
        if code and name:
            node = {"code": code, "name": name, "children": []}
            l2_nodes[code] = node
            if parent in l1_nodes:
                l1_nodes[parent]["children"].append(node)

    for rec in levels.get("L3", []):
        code = str(rec.get("index_code", "")).strip()
        name = str(rec.get("industry_name", "")).strip()
        parent = str(rec.get("parent_code", "")).strip()
        if code and name:
            node = {"code": code, "name": name, "symbols": []}
            if parent in l2_nodes:
                l2_nodes[parent]["children"].append(node)

    return list(l1_nodes.values())


def get_sw_industry_tree() -> list[dict]:
    global _sw_tree_cache
    if _sw_tree_cache is not None:
        return _sw_tree_cache
    return []


async def refresh_sw_industry_tree() -> list[dict]:
    """Load (or reload) the SW tree from TuShare and cache it."""
    global _sw_tree_cache
    try:
        _sw_tree_cache = await _load_sw_tree_from_tushare()
        logger.info("SW industry tree loaded: %d L1 nodes", len(_sw_tree_cache))
    except Exception:
        logger.warning("Failed to load SW industry tree from TuShare", exc_info=True)
        if _sw_tree_cache is None:
            _sw_tree_cache = []
    return _sw_tree_cache


# ---------------------------------------------------------------------------
# SW tree navigation helpers
# ---------------------------------------------------------------------------


def get_sw_level1(level1_code: str) -> dict | None:
    tree = get_sw_industry_tree()
    return next((node for node in tree if node["code"] == level1_code), None)


def get_sw_level2(level1_code: str, level2_code: str) -> dict | None:
    level1 = get_sw_level1(level1_code)
    if level1 is None:
        return None
    return next((node for node in level1["children"] if node["code"] == level2_code), None)


def get_sw_level3(level1_code: str, level2_code: str, level3_code: str) -> dict | None:
    level2 = get_sw_level2(level1_code, level2_code)
    if level2 is None:
        return None
    return next((node for node in level2["children"] if node["code"] == level3_code), None)


def list_symbols_by_level1(level1_code: str) -> list[str]:
    level1 = get_sw_level1(level1_code)
    if level1 is None:
        return []
    symbols: list[str] = []
    for level2 in level1["children"]:
        for level3 in level2["children"]:
            symbols.extend(level3.get("symbols", []))
    return symbols


def list_symbols_by_level2(level1_code: str, level2_code: str) -> list[str]:
    level2 = get_sw_level2(level1_code, level2_code)
    if level2 is None:
        return []
    symbols: list[str] = []
    for level3 in level2["children"]:
        symbols.extend(level3.get("symbols", []))
    return symbols


def list_symbols_by_level3(level1_code: str, level2_code: str, level3_code: str) -> list[str]:
    level3 = get_sw_level3(level1_code, level2_code, level3_code)
    if level3 is None:
        return []
    return list(level3.get("symbols", []))


async def list_stocks_by_symbols(db: AsyncSession, symbols: list[str]) -> list[StockOut]:
    if not symbols:
        return []
    rows = (
        await db.execute(select(Stock).where(Stock.symbol.in_(symbols)))
    ).scalars().all()
    if not rows:
        return []
    stock_by_symbol: dict[str, StockOut] = {}
    for row in rows:
        stock_by_symbol.setdefault(row.symbol, StockOut.model_validate(row))
    return [stock_by_symbol[symbol] for symbol in symbols if symbol in stock_by_symbol]
