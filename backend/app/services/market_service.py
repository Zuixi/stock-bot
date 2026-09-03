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

from sqlalchemy import func, select, text, union
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
# SW Industry tree — DB-backed, built from local XLS imports
# ---------------------------------------------------------------------------


async def get_sw_industry_tree(cache: CacheClient | None = None) -> list[dict]:
    """Build the three-level SW industry tree from DB with stock counts.

    Returns a nested structure:
    [{ code, name, stockCount, children: [{ code, name, stockCount, children: [{ code, name, stockCount, symbols }] }] }]
    """
    cache_key = "market:sw-tree"
    if cache:
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

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
            select(SwIndustryMember.industry_code, SwIndustryMember.symbol)
            .join(Stock, Stock.symbol == SwIndustryMember.symbol)
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
        categorized_symbols_subq = (
            union(
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
        )
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
            l2_symbol_sets[cls.industry_code] = set(custom_l2_symbols_by_code.get(cls.industry_code, set()))
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


async def get_sw_level3(
    level1_code: str, level2_code: str, level3_code: str
) -> dict | None:
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
                await db.execute(
                    select(Stock.symbol)
                    .outerjoin(
                        categorized_symbols_subq,
                        categorized_symbols_subq.c.symbol == Stock.symbol,
                    )
                    .where(categorized_symbols_subq.c.symbol.is_(None))
                    .order_by(Stock.exchange, Stock.symbol)
                )
            ).scalars().all()
            return list(uncategorized)

        # L1 -> L2 codes -> L3 codes -> members
        l2_codes = (
            await db.execute(
                select(SwIndustryClass.industry_code).where(
                    SwIndustryClass.parent_code == level1_code,
                    SwIndustryClass.level == 2,
                )
            )
        ).scalars().all()
        if not l2_codes:
            return []
        l3_codes = (
            await db.execute(
                select(SwIndustryClass.industry_code).where(
                    SwIndustryClass.parent_code.in_(l2_codes),
                    SwIndustryClass.level == 3,
                )
            )
        ).scalars().all()
        if not l3_codes:
            return []
        official_symbols = (
            await db.execute(
                select(SwIndustryMember.symbol)
                .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                .where(
                    SwIndustryMember.industry_code.in_(l3_codes)
                )
            )
        ).scalars().all()
        custom_l2_symbols = (
            await db.execute(
                select(StockCustomSwTag.symbol)
                .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                .where(StockCustomSwTag.industry_code.in_(l2_codes))
            )
        ).scalars().all()
        custom_l3_symbols = (
            await db.execute(
                select(StockCustomSwTag.symbol)
                .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                .where(StockCustomSwTag.industry_code.in_(l3_codes))
            )
        ).scalars().all()
        symbol_set = set(official_symbols) | set(custom_l2_symbols) | set(custom_l3_symbols)
        ordered = (
            await db.execute(
                select(Stock.symbol)
                .where(Stock.symbol.in_(symbol_set))
                .order_by(Stock.exchange, Stock.symbol)
            )
        ).scalars().all()
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
            await db.execute(
                select(SwIndustryClass.industry_code).where(
                    SwIndustryClass.parent_code == level2_code,
                    SwIndustryClass.level == 3,
                )
            )
        ).scalars().all()
        if not l3_codes:
            return []
        official_symbols = (
            await db.execute(
                select(SwIndustryMember.symbol)
                .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                .where(
                    SwIndustryMember.industry_code.in_(l3_codes)
                )
            )
        ).scalars().all()
        custom_l2_symbols = (
            await db.execute(
                select(StockCustomSwTag.symbol)
                .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                .where(StockCustomSwTag.industry_code == level2_code)
            )
        ).scalars().all()
        custom_l3_symbols = (
            await db.execute(
                select(StockCustomSwTag.symbol)
                .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                .where(StockCustomSwTag.industry_code.in_(l3_codes))
            )
        ).scalars().all()
        symbol_set = set(official_symbols) | set(custom_l2_symbols) | set(custom_l3_symbols)
        ordered = (
            await db.execute(
                select(Stock.symbol)
                .where(Stock.symbol.in_(symbol_set))
                .order_by(Stock.exchange, Stock.symbol)
            )
        ).scalars().all()
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
            await db.execute(
                select(SwIndustryMember.symbol)
                .join(Stock, Stock.symbol == SwIndustryMember.symbol)
                .where(
                    SwIndustryMember.industry_code.in_(l3_codes)
                )
            )
        ).scalars().all()
        custom_symbols = (
            await db.execute(
                select(StockCustomSwTag.symbol)
                .join(Stock, Stock.symbol == StockCustomSwTag.symbol)
                .where(StockCustomSwTag.industry_code.in_(l3_codes))
            )
        ).scalars().all()
        symbol_set = set(official_symbols) | set(custom_symbols)
        ordered = (
            await db.execute(
                select(Stock.symbol)
                .where(Stock.symbol.in_(symbol_set))
                .order_by(Stock.exchange, Stock.symbol)
            )
        ).scalars().all()
    return list(ordered)


async def list_symbols_by_level3(
    level1_code: str, level2_code: str, level3_code: str
) -> list[str]:
    """Get all member symbols under a level-3 industry."""
    return await list_symbols_by_industry_codes([level3_code])


def _uncategorized_symbols_subquery():
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


def _effective_industry():
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
    rows = (
        await db.execute(select(Stock).where(Stock.symbol.in_(symbols)))
    ).scalars().all()
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
    q.volume    AS volume,
    q.amount    AS amount,
    q2.close    AS prev_close,
    d.pe_ttm    AS pe_ttm,
    d.pb        AS pb,
    d.total_mv  AS total_mv,
    d.circ_mv   AS circ_mv,
    d.turnover_rate AS turnover_rate
FROM stocks s
LEFT JOIN LATERAL (
    SELECT close, volume, amount, trade_date
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
    db: AsyncSession, symbols: list[str],
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
            data["change_percent"] = round(
                (float(lp) - float(pc)) / float(pc) * 100, 2
            )
        stock = StockEnrichedOut(**data)
        stock_by_symbol.setdefault(stock.symbol, stock)

    return [stock_by_symbol[sym] for sym in symbols if sym in stock_by_symbol]
