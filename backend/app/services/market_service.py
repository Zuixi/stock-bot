"""Market service: provide market dashboard data for frontend."""

from __future__ import annotations

from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.stock import Stock
from app.schemas.stock import StockOut

HotBoardCategory = Literal["industry", "concept", "region"]

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

HOT_BOARDS = {
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
                    {
                        "code": "l3-brokerage",
                        "name": "证券",
                        "symbols": ["600030", "300059"],
                    },
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
                    {
                        "code": "l3-consumer-electronics",
                        "name": "消费电子",
                        "symbols": ["002475"],
                    },
                ],
            },
            {
                "code": "l2-computer",
                "name": "计算机",
                "children": [
                    {"code": "l3-office-software", "name": "办公软件", "symbols": ["688111"]},
                    {
                        "code": "l3-industry-software",
                        "name": "行业软件",
                        "symbols": ["002415", "830799"],
                    },
                ],
            },
            {
                "code": "l2-power-equipment",
                "name": "电力设备",
                "children": [
                    {
                        "code": "l3-lithium-chain",
                        "name": "锂电产业链",
                        "symbols": ["601012", "300750", "430139"],
                    }
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
                    {
                        "code": "l3-industrial-control",
                        "name": "工控自动化",
                        "symbols": ["300124"],
                    },
                    {
                        "code": "l3-precision-manufacturing",
                        "name": "精密制造",
                        "symbols": ["430510"],
                    },
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


def list_market_indices() -> list[dict]:
    return MARKET_INDICES


def get_distribution() -> list[dict]:
    return MARKET_DISTRIBUTION


def get_sectors() -> list[dict]:
    return MARKET_SECTORS


def get_capital_flow() -> list[dict]:
    return MARKET_CAPITAL_FLOW


def get_hot_boards(category: HotBoardCategory) -> list[dict]:
    return HOT_BOARDS.get(category, [])


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
