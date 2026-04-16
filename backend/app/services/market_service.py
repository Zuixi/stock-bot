"""Market service: provide market dashboard data for frontend.

Data sources
------------
- **TuShare Pro** (via ``TuShareClient``): index daily, daily quotes.
- **PostgreSQL**: aggregated from ``stocks`` + ``daily_quotes`` tables.
- **Static fallback**: last-resort data when both TuShare and DB return empty.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import async_session_factory
from app.models.quote import DailyQuote
from app.models.stock import Stock
from app.schemas.stock import StockOut

logger = logging.getLogger(__name__)

HotBoardCategory = Literal["industry", "concept", "region"]

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

# ---------------------------------------------------------------------------
# Static fallback data — returned only when TuShare AND DB are both empty
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


async def list_market_indices() -> list[dict[str, Any]]:
    """Return main market index snapshots via TuShare index_daily."""
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
                "name": idx["name"],
                "value": round(close, 2),
                "change": change,
                "changePercent": change_pct,
                "exchange": idx["exchange"],
                "asof": asof,
            })
        except Exception:
            logger.warning("list_market_indices: failed for %s", idx["ts_code"], exc_info=True)

    return results


async def get_distribution() -> list[dict[str, Any]]:
    """Return market-wide up/down distribution from DB daily_quotes.

    Buckets stocks by pct_chg into standard ranges.
    """
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
    return [{"range": r, "count": db_rows.get(r, 0)} for r in ordered_ranges]


async def get_sectors() -> list[dict[str, Any]]:
    """Return industry sector performance from DB.

    Groups stocks by ``csrc_desc`` (industry from stock_basic), then
    aggregates latest daily_quotes to compute sector-level change %.
    """
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
    return sectors


async def get_capital_flow() -> list[dict[str, Any]]:
    """Return sector-level turnover distribution from DB.

    Uses total amount traded per industry as a proxy for capital flow.
    """
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
    return flows


async def get_hot_boards(category: HotBoardCategory) -> list[dict[str, Any]]:
    """Return hot boards for the given category from DB.

    - industry: groups by ``csrc_desc``
    - region:   groups by ``province``
    - concept:  not available from stock_basic; returns empty
    """
    if category == "concept":
        return []

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
    return boards


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
