"""Market service: provide market dashboard data for frontend.

Data sources
------------
- **AKShare** (async, via ``asyncio.to_thread``): live indices, distribution,
  sector rankings, capital flow, hot boards.
- **Mock fallback**: static data used when AKShare is unavailable (network
  error, import failure, etc.) so the frontend never gets a hard 500.

CNINFO paid-API integration points are marked with ``# CNINFO-PAID:`` comments.
Once a token is configured, those lines can replace the AKShare calls.

See: backend/docs/cninfo_api.md — Section 3 (paid endpoints) and Section 4
(degradation strategy table).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.schemas.stock import StockOut

logger = logging.getLogger(__name__)

HotBoardCategory = Literal["industry", "concept", "region"]

# ---------------------------------------------------------------------------
# Static mock data — used as fallback when live fetch fails
# ---------------------------------------------------------------------------

MARKET_INDICES = [
    {"code": "000001", "name": "上证指数", "value": 3880.10, "change": -39.19, "changePercent": -1.00, "exchange": "Shanghai_Stocks", "asof": "2026-04-07T15:00:00Z"},
    {"code": "399001", "name": "深证成指", "value": 13352.90, "change": -134.04, "changePercent": -0.99, "exchange": "Shenzen_Stocks", "asof": "2026-04-07T15:00:00Z"},
    {"code": "399006", "name": "创业板指", "value": 2754.38, "change": 12.56, "changePercent": 0.46, "exchange": "Shenzen_Stocks", "asof": "2026-04-07T15:00:00Z"},
    {"code": "899050", "name": "北证50", "value": 1128.65, "change": -8.32, "changePercent": -0.73, "exchange": "Beijing_Stocks", "asof": "2026-04-07T15:00:00Z"},
    {"code": "000016", "name": "上证50", "value": 2816.53, "change": -17.50, "changePercent": -0.62, "exchange": "Shanghai_Stocks", "asof": "2026-04-07T15:00:00Z"},
    {"code": "000300", "name": "沪深300", "value": 4452.99, "change": -31.94, "changePercent": -0.71, "exchange": "Shanghai_Stocks", "asof": "2026-04-07T15:00:00Z"},
]

MARKET_DISTRIBUTION = [
    {"range": "跌停", "count": 38},
    {"range": ">-7%", "count": 56},
    {"range": "-5~-7%", "count": 215},
    {"range": "-3~-5%", "count": 260},
    {"range": "-1~-3%", "count": 27},
    {"range": "0~-1%", "count": 481},
    {"range": "0~1%", "count": 3618},
    {"range": "1~3%", "count": 42},
    {"range": "3~5%", "count": 41},
    {"range": ">5%", "count": 18},
    {"range": "涨停", "count": 32},
]

MARKET_SECTORS = [
    {"name": "银行", "changePercent": -0.74, "totalMarketCap": 12.5e12, "stockCount": 42, "topStocks": [{"symbol": "601398", "name": "工商银行", "changePercent": -0.85}, {"symbol": "601939", "name": "建设银行", "changePercent": -0.62}]},
    {"name": "电子", "changePercent": 2.15, "totalMarketCap": 8.3e12, "stockCount": 385, "topStocks": [{"symbol": "002475", "name": "立讯精密", "changePercent": 3.21}, {"symbol": "603501", "name": "韦尔股份", "changePercent": 2.87}]},
    {"name": "医药生物", "changePercent": -0.24, "totalMarketCap": 7.8e12, "stockCount": 420, "topStocks": [{"symbol": "600276", "name": "恒瑞医药", "changePercent": -1.02}, {"symbol": "300760", "name": "迈瑞医疗", "changePercent": 0.53}]},
    {"name": "食品饮料", "changePercent": -0.03, "totalMarketCap": 6.2e12, "stockCount": 115, "topStocks": [{"symbol": "600519", "name": "贵州茅台", "changePercent": 0.12}, {"symbol": "000858", "name": "五粮液", "changePercent": -0.35}]},
    {"name": "电力设备", "changePercent": 1.56, "totalMarketCap": 5.9e12, "stockCount": 342, "topStocks": [{"symbol": "300750", "name": "宁德时代", "changePercent": 2.45}, {"symbol": "601012", "name": "隆基绿能", "changePercent": 1.23}]},
    {"name": "计算机", "changePercent": 3.42, "totalMarketCap": 4.1e12, "stockCount": 312, "topStocks": [{"symbol": "002415", "name": "海康威视", "changePercent": 1.89}, {"symbol": "688111", "name": "金山办公", "changePercent": 4.56}]},
]

MARKET_CAPITAL_FLOW = [
    {"name": "银行", "inflow": 18.5, "outflow": -17.1},
    {"name": "酿酒", "inflow": 12.3, "outflow": -8.9},
    {"name": "电力设备", "inflow": 14.2, "outflow": -6.3},
    {"name": "半导体", "inflow": 22.8, "outflow": -15.2},
    {"name": "汽车", "inflow": 9.5, "outflow": -7.2},
    {"name": "医药", "inflow": 8.1, "outflow": -11.4},
]

HOT_BOARDS: dict[str, list[dict]] = {
    "industry": [
        {"id": "ind-power", "name": "电力", "code": "630700", "changePercent": 3.93, "upCount": 97, "flatCount": 1, "downCount": 9, "leaders": [{"symbol": "600995", "name": "南网能源", "changePercent": 10.02}, {"symbol": "600905", "name": "三峡能源", "changePercent": 5.21}]},
        {"id": "ind-agri", "name": "农机装备", "code": "610200", "changePercent": 3.26, "upCount": 15, "flatCount": 0, "downCount": 1, "leaders": [{"symbol": "601038", "name": "一拖股份", "changePercent": 8.12}, {"symbol": "300159", "name": "新研股份", "changePercent": 4.08}]},
    ],
    "concept": [
        {"id": "con-ai-agent", "name": "AI Agent", "code": "GN001", "changePercent": 4.21, "upCount": 68, "flatCount": 3, "downCount": 9, "leaders": [{"symbol": "688111", "name": "金山办公", "changePercent": 6.32}, {"symbol": "300033", "name": "同花顺", "changePercent": 5.15}]},
        {"id": "con-robot", "name": "人形机器人", "code": "GN002", "changePercent": 3.74, "upCount": 54, "flatCount": 2, "downCount": 11, "leaders": [{"symbol": "300124", "name": "汇川技术", "changePercent": 4.25}, {"symbol": "002050", "name": "三花智控", "changePercent": 3.11}]},
    ],
    "region": [
        {"id": "reg-yangtze", "name": "长三角", "code": "DQ001", "changePercent": 1.86, "upCount": 102, "flatCount": 6, "downCount": 24, "leaders": [{"symbol": "600570", "name": "恒生电子", "changePercent": 3.21}, {"symbol": "600309", "name": "万华化学", "changePercent": 2.57}]},
        {"id": "reg-greater-bay", "name": "粤港澳大湾区", "code": "DQ002", "changePercent": 1.74, "upCount": 89, "flatCount": 4, "downCount": 30, "leaders": [{"symbol": "000333", "name": "美的集团", "changePercent": 2.66}, {"symbol": "002594", "name": "比亚迪", "changePercent": 2.33}]},
    ],
}

SW_INDUSTRY_TREE = [
    {
        "code": "l1-consumer",
        "name": "大消费",
        "children": [
            {
                "code": "l2-food-beverage",
                "name": "食品饮料",
                "children": [
                    {"code": "l3-liquor", "name": "白酒", "symbols": ["600519", "000858"]},
                    {"code": "l3-soft-drink", "name": "软饮料", "symbols": []},
                ],
            },
            {
                "code": "l2-home-appliance",
                "name": "家用电器",
                "children": [
                    {"code": "l3-white-goods", "name": "白电", "symbols": ["000333"]},
                    {"code": "l3-small-appliance", "name": "小家电", "symbols": []},
                ],
            },
        ],
    },
    {
        "code": "l1-finance-real-estate",
        "name": "金融地产",
        "children": [
            {
                "code": "l2-banks",
                "name": "银行",
                "children": [
                    {"code": "l3-state-owned-banks", "name": "国有大行", "symbols": ["601398"]},
                    {"code": "l3-joint-stock-banks", "name": "股份制银行", "symbols": ["600036"]},
                ],
            },
            {
                "code": "l2-non-bank-finance",
                "name": "非银金融",
                "children": [
                    {"code": "l3-insurance", "name": "保险", "symbols": ["601318"]},
                    {"code": "l3-brokerage", "name": "证券", "symbols": ["600030", "300059"]},
                ],
            },
            {
                "code": "l2-real-estate",
                "name": "房地产",
                "children": [
                    {"code": "l3-residential-dev", "name": "住宅开发", "symbols": ["000002"]}
                ],
            },
        ],
    },
    {
        "code": "l1-tech-manufacture",
        "name": "科技制造",
        "children": [
            {
                "code": "l2-electronics",
                "name": "电子",
                "children": [
                    {"code": "l3-semiconductor", "name": "半导体", "symbols": ["688981"]},
                    {"code": "l3-consumer-electronics", "name": "消费电子", "symbols": ["002475"]},
                ],
            },
            {
                "code": "l2-computer",
                "name": "计算机",
                "children": [
                    {"code": "l3-office-software", "name": "办公软件", "symbols": ["688111"]},
                    {"code": "l3-industry-software", "name": "行业软件", "symbols": ["002415", "830799"]},
                ],
            },
            {
                "code": "l2-power-equipment",
                "name": "电力设备",
                "children": [
                    {"code": "l3-lithium-chain", "name": "锂电产业链", "symbols": ["601012", "300750", "430139"]}
                ],
            },
            {
                "code": "l2-automobile",
                "name": "汽车",
                "children": [
                    {"code": "l3-nev", "name": "新能源整车", "symbols": ["002594"]}
                ],
            },
            {
                "code": "l2-machinery",
                "name": "机械设备",
                "children": [
                    {"code": "l3-industrial-control", "name": "工控自动化", "symbols": ["300124"]},
                    {"code": "l3-precision-manufacturing", "name": "精密制造", "symbols": ["430510"]},
                ],
            },
        ],
    },
    {
        "code": "l1-healthcare",
        "name": "医药健康",
        "children": [
            {
                "code": "l2-pharma-biotech",
                "name": "医药生物",
                "children": [
                    {"code": "l3-chemical-pharma", "name": "化学制药", "symbols": ["600276"]},
                    {"code": "l3-medical-devices", "name": "医疗器械", "symbols": ["300760"]},
                    {"code": "l3-biopharma", "name": "生物制药", "symbols": ["430047"]},
                ],
            }
        ],
    },
    {
        "code": "l1-utilities-energy",
        "name": "公用事业与能源",
        "children": [
            {
                "code": "l2-utilities",
                "name": "公用事业",
                "children": [
                    {"code": "l3-hydropower", "name": "水电运营", "symbols": ["600900"]}
                ],
            }
        ],
    },
]

# ---------------------------------------------------------------------------
# AKShare sync helpers (run in thread pool via asyncio.to_thread)
# ---------------------------------------------------------------------------

# Main index codes we want from CNINFO p_index2905
_TARGET_INDEX_CODES = {"000001", "000016", "000300", "399001", "399006", "899050"}

# Map AKShare sina-style codes (e.g. "sh000001") to canonical exchange labels
_AKSHARE_CODE_META: dict[str, dict[str, str]] = {
    "sh000001": {"code": "000001", "exchange": "Shanghai_Stocks"},
    "sh000016": {"code": "000016", "exchange": "Shanghai_Stocks"},
    "sh000300": {"code": "000300", "exchange": "Shanghai_Stocks"},
    "sh000905": {"code": "000905", "exchange": "Shanghai_Stocks"},
    "sz399001": {"code": "399001", "exchange": "Shenzen_Stocks"},
    "sz399006": {"code": "399006", "exchange": "Shenzen_Stocks"},
    "bj899050": {"code": "899050", "exchange": "Beijing_Stocks"},
}


async def _fetch_indices_cninfo() -> list[dict[str, Any]]:
    """Fetch main A-share index quotes via CNINFO p_index2905.

    API ref: docs/references/cninfo/指数API/交易所指数日行情API.md
    Endpoint: GET http://webapi.cninfo.com.cn/api/index/p_index2905
    Required param: edate (today's date)
    Returns empty list if token missing or API unavailable.
    """
    from datetime import datetime  # noqa: PLC0415

    from app.core.providers.cninfo_client import get_cninfo_client  # noqa: PLC0415

    client = get_cninfo_client()
    today = datetime.now().date()
    records = await client.get_index_daily(edate=today)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in records:
        code = r.get("code", "")
        if code not in _TARGET_INDEX_CODES or code in seen:
            continue
        seen.add(code)
        results.append({
            "code": code,
            "name": r.get("name", code),
            "value": r.get("close"),
            "change": r.get("change"),
            "changePercent": r.get("changePercent"),
            "exchange": r.get("exchange", ""),
            "asof": r.get("trade_date").isoformat() if r.get("trade_date") else None,
        })

    return results  # empty list signals caller to try next tier


def _fetch_indices_akshare() -> list[dict[str, Any]]:
    """Fetch main A-share index quotes via AKShare (second-tier fallback).

    AKShare function: ``ak.stock_zh_index_spot_sina()``
    Returns all Sina-tracked indices; we filter to ``_AKSHARE_CODE_META`` codes.
    """
    import akshare as ak  # noqa: PLC0415

    df = ak.stock_zh_index_spot_sina()
    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        raw_code = str(row.get("代码", "")).strip().lower()
        meta = _AKSHARE_CODE_META.get(raw_code)
        if meta is None:
            continue
        try:
            value = float(row.get("最新价") or 0)
            change = float(row.get("涨跌额") or 0)
            change_pct = float(row.get("涨跌幅") or 0)
        except (TypeError, ValueError):
            continue
        results.append({
            "code": meta["code"],
            "name": str(row.get("名称", "")).strip(),
            "value": round(value, 2),
            "change": round(change, 2),
            "changePercent": round(change_pct, 2),
            "exchange": meta["exchange"],
            "asof": None,
        })
    return results


def _fetch_distribution_akshare() -> list[dict[str, Any]]:
    """Fetch market-wide up/down distribution via AKShare.

    AKShare function: ``ak.stock_market_activity_legu()``
    Returns counts by change-percent bucket.

    # CNINFO-PAID: Replace with CNINFO 行情中心-市场统计 endpoint when token is available.
    """
    import akshare as ak  # noqa: PLC0415

    df = ak.stock_market_activity_legu()
    # Columns vary by AKShare version; map to frontend buckets best-effort.
    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        range_label = str(row.get("涨跌幅区间", row.get("区间", ""))).strip()
        count = row.get("数量", row.get("家数", 0))
        if range_label and count is not None:
            try:
                results.append({"range": range_label, "count": int(count)})
            except (TypeError, ValueError):
                pass
    return results if results else MARKET_DISTRIBUTION


def _fetch_sectors_akshare() -> list[dict[str, Any]]:
    """Fetch industry sector rankings via AKShare.

    AKShare function: ``ak.stock_board_industry_name_em()``
    Returns East-Money industry board data.

    # CNINFO-PAID: Replace with CNINFO 板块数据-行业行情 endpoint when token is available.
    """
    import akshare as ak  # noqa: PLC0415

    df = ak.stock_board_industry_name_em()
    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        try:
            change_pct = float(row.get("涨跌幅", 0) or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        try:
            stock_count = int(row.get("成分股数量", row.get("股票数量", 0)) or 0)
        except (TypeError, ValueError):
            stock_count = 0
        name = str(row.get("板块名称", row.get("名称", ""))).strip()
        if not name:
            continue
        results.append(
            {
                "name": name,
                "changePercent": round(change_pct, 2),
                "totalMarketCap": None,
                "stockCount": stock_count,
                "topStocks": [],
            }
        )
    results.sort(key=lambda x: abs(x["changePercent"]), reverse=True)
    return results[:20] if results else MARKET_SECTORS


def _fetch_capital_flow_akshare() -> list[dict[str, Any]]:
    """Fetch sector capital-flow data via AKShare.

    AKShare function: ``ak.stock_fund_flow_industry()``
    Returns net fund flow by industry.

    # CNINFO-PAID: Replace with CNINFO 专题统计-资金流向 endpoint when token is available.
    """
    import akshare as ak  # noqa: PLC0415

    df = ak.stock_fund_flow_industry(symbol="今日")
    cols = list(df.columns)
    # Detect name column flexibly
    name_col = next(
        (c for c in cols if "行业" in c or "板块" in c or "名称" in c), None
    )
    # Detect net-inflow column flexibly (主力净流入 / 净额 / 净流入)
    flow_col = next(
        (c for c in cols if "净额" in c or "净流入" in c), None
    )
    if name_col is None:
        logger.warning("_fetch_capital_flow_akshare: cannot find name column in %s", cols)
        return MARKET_CAPITAL_FLOW

    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name:
            continue
        inflow = 0.0
        if flow_col:
            try:
                inflow = float(row.get(flow_col, 0) or 0)
            except (TypeError, ValueError):
                inflow = 0.0
        results.append(
            {
                "name": name,
                "inflow": round(inflow / 1e8, 2),
                "outflow": round(-abs(inflow) / 1e8, 2) if inflow < 0 else 0.0,
            }
        )
    results.sort(key=lambda x: x["inflow"], reverse=True)
    return results[:10] if results else MARKET_CAPITAL_FLOW


def _fetch_hot_boards_akshare(category: HotBoardCategory) -> list[dict[str, Any]]:
    """Fetch hot board data for industry / concept / region via AKShare.

    AKShare functions:
    - industry: ``ak.stock_board_industry_name_em()``
    - concept:  ``ak.stock_board_concept_name_em()``
    - region:   ``ak.stock_board_spot_em()`` (地域板块)

    # CNINFO-PAID: Replace with CNINFO 行情中心-涨幅排行 endpoint when token is available.
    """
    import akshare as ak  # noqa: PLC0415

    if category == "industry":
        df = ak.stock_board_industry_name_em()
        name_col = "板块名称"
        code_col = "板块代码"
        up_col = "上涨家数"
        down_col = "下跌家数"
        flat_col = None
    elif category == "concept":
        df = ak.stock_board_concept_name_em()
        name_col = "板块名称"
        code_col = "板块代码"
        up_col = "上涨家数"
        down_col = "下跌家数"
        flat_col = None
    else:
        df = ak.stock_board_spot_em(symbol="地域板块")
        cols = list(df.columns)
        name_col = next((c for c in cols if "名称" in c or "板块" in c), "板块名称")
        code_col = next((c for c in cols if "代码" in c), "板块代码")
        up_col = next((c for c in cols if "上涨" in c), "上涨家数")
        down_col = next((c for c in cols if "下跌" in c), "下跌家数")
        flat_col = None

    results: list[dict[str, Any]] = []
    for _, row in df.iterrows():
        name = str(row.get(name_col, "")).strip()
        if not name:
            continue
        try:
            change_pct = float(row.get("涨跌幅", 0) or 0)
        except (TypeError, ValueError):
            change_pct = 0.0
        try:
            up = int(row.get(up_col, 0) or 0)
        except (TypeError, ValueError):
            up = 0
        try:
            down = int(row.get(down_col, 0) or 0)
        except (TypeError, ValueError):
            down = 0
        flat = 0
        if flat_col:
            try:
                flat = int(row.get(flat_col, 0) or 0)
            except (TypeError, ValueError):
                flat = 0
        code = str(row.get(code_col, "")).strip()
        results.append(
            {
                "id": f"{category}-{code}",
                "name": name,
                "code": code,
                "changePercent": round(change_pct, 2),
                "upCount": up,
                "flatCount": flat,
                "downCount": down,
                "leaders": [],
            }
        )
    results.sort(key=lambda x: x["changePercent"], reverse=True)
    return results[:10] if results else HOT_BOARDS.get(category, [])


# ---------------------------------------------------------------------------
# Public async service methods
# ---------------------------------------------------------------------------


async def list_market_indices() -> list[dict[str, Any]]:
    """Return main market index snapshots.

    Degradation tiers
    -----------------
    1. CNINFO ``p_index2905`` (docs/references/cninfo/指数API/交易所指数日行情API.md)
       — requires ``CNINFO_TOKEN``; returns empty list when token is absent.
    2. AKShare ``stock_zh_index_spot_sina`` — free, runs in thread pool.
    3. Static mock ``MARKET_INDICES`` — last resort, always succeeds.
    """
    # Tier 1: CNINFO
    try:
        records = await _fetch_indices_cninfo()
        if records:
            return records
        logger.info("list_market_indices: CNINFO returned no data, trying AKShare")
    except Exception:
        logger.warning("list_market_indices: CNINFO error, trying AKShare", exc_info=True)

    # Tier 2: AKShare
    try:
        records = await asyncio.to_thread(_fetch_indices_akshare)
        if records:
            return records
        logger.warning("list_market_indices: AKShare returned no data, using mock")
    except Exception:
        logger.warning("list_market_indices: AKShare error, using mock", exc_info=True)

    # Tier 3: static mock
    return MARKET_INDICES


async def get_distribution() -> list[dict[str, Any]]:
    """Return market-wide up/down distribution buckets.

    Tries AKShare first; falls back to static mock on any error.
    """
    try:
        return await asyncio.to_thread(_fetch_distribution_akshare)
    except Exception:
        logger.warning("get_distribution: AKShare fetch failed, using mock", exc_info=True)
        return MARKET_DISTRIBUTION


async def get_sectors() -> list[dict[str, Any]]:
    """Return industry sector performance summary.

    Tries AKShare first; falls back to static mock on any error.
    """
    try:
        return await asyncio.to_thread(_fetch_sectors_akshare)
    except Exception:
        logger.warning("get_sectors: AKShare fetch failed, using mock", exc_info=True)
        return MARKET_SECTORS


async def get_capital_flow() -> list[dict[str, Any]]:
    """Return sector capital-flow data (inflow / outflow in 100M CNY).

    Tries AKShare first; falls back to static mock on any error.
    """
    try:
        return await asyncio.to_thread(_fetch_capital_flow_akshare)
    except Exception:
        logger.warning("get_capital_flow: AKShare fetch failed, using mock", exc_info=True)
        return MARKET_CAPITAL_FLOW


async def get_hot_boards(category: HotBoardCategory) -> list[dict[str, Any]]:
    """Return hot boards for the given category (industry / concept / region).

    Tries AKShare first; falls back to static mock on any error.
    """
    try:
        return await asyncio.to_thread(_fetch_hot_boards_akshare, category)
    except Exception:
        logger.warning(
            "get_hot_boards(%s): AKShare fetch failed, using mock", category, exc_info=True
        )
        return HOT_BOARDS.get(category, [])


# ---------------------------------------------------------------------------
# SW Industry tree helpers (sync, in-memory)
# ---------------------------------------------------------------------------


def get_sw_industry_tree() -> list[dict]:
    return SW_INDUSTRY_TREE


def get_sw_level1(level1_code: str) -> dict | None:
    return next((node for node in SW_INDUSTRY_TREE if node["code"] == level1_code), None)


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
            symbols.extend(level3["symbols"])
    return symbols


def list_symbols_by_level2(level1_code: str, level2_code: str) -> list[str]:
    level2 = get_sw_level2(level1_code, level2_code)
    if level2 is None:
        return []
    symbols: list[str] = []
    for level3 in level2["children"]:
        symbols.extend(level3["symbols"])
    return symbols


def list_symbols_by_level3(level1_code: str, level2_code: str, level3_code: str) -> list[str]:
    level3 = get_sw_level3(level1_code, level2_code, level3_code)
    if level3 is None:
        return []
    return list(level3["symbols"])


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
