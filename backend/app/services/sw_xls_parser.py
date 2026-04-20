"""Parse Shenwan industry classification from local XLS/XLSX files.

Data sources:
- SwClassCode_2021.xls: three-level industry tree (行业代码, 一级行业名称, 二级行业名称, 三级行业名称)
- 最新个股行业分类.xlsx: stock-to-L3 industry mapping (交易所, 行业代码, 股票代码, ...)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TypedDict

logger = logging.getLogger(__name__)


class SwClassRow(TypedDict):
    industry_code: str
    level: int
    industry_name: str
    parent_code: str | None


class SwMemberRow(TypedDict):
    industry_code: str
    stock_code: str
    symbol: str
    stock_name: str


def parse_sw_classes(filepath: str | Path) -> list[SwClassRow]:
    """Parse SwClassCode_2021.xls into a flat list of classification nodes.

    Determines level from which name columns are populated:
    - L3 name present -> level 3
    - L2 name present (L3 empty) -> level 2
    - Otherwise -> level 1

    Parent code is derived from the industry_code structure:
    - L1: parent_code = None
    - L2: parent_code = first 2 digits + "0000"
    - L3: parent_code = first 4 digits + "00"
    """
    import xlrd  # noqa: PLC0415

    wb = xlrd.open_workbook(str(filepath))
    ws = wb.sheet_by_index(0)

    results: list[SwClassRow] = []
    for row_idx in range(1, ws.nrows):
        raw_code = str(ws.cell_value(row_idx, 0)).strip()
        l1_name = str(ws.cell_value(row_idx, 1)).strip()
        l2_name = str(ws.cell_value(row_idx, 2)).strip()
        l3_name = str(ws.cell_value(row_idx, 3)).strip()

        if not raw_code:
            continue

        if l3_name:
            level = 3
            industry_name = l3_name
            parent_code = raw_code[:4] + "00"
        elif l2_name:
            level = 2
            industry_name = l2_name
            parent_code = raw_code[:2] + "0000"
        else:
            level = 1
            industry_name = l1_name
            parent_code = None

        results.append(SwClassRow(
            industry_code=raw_code,
            level=level,
            industry_name=industry_name,
            parent_code=parent_code,
        ))

    logger.info(
        "Parsed %d SW classification nodes (L1=%d, L2=%d, L3=%d)",
        len(results),
        sum(1 for r in results if r["level"] == 1),
        sum(1 for r in results if r["level"] == 2),
        sum(1 for r in results if r["level"] == 3),
    )
    return results


def parse_sw_members(filepath: str | Path) -> list[SwMemberRow]:
    """Parse 最新个股行业分类.xlsx into a flat list of stock-to-industry mappings.

    Only A-share stocks (交易所 == "A股") are included.
    Stock codes like "600373.SH" are split to extract the pure symbol "600373".
    """
    import openpyxl  # noqa: PLC0415

    wb = openpyxl.load_workbook(str(filepath), read_only=True, data_only=True)
    ws = wb.active

    results: list[SwMemberRow] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        exchange = str(row[0]).strip() if row[0] else ""
        if exchange != "A股":
            continue

        industry_code = str(row[1]).strip() if row[1] else ""
        stock_code = str(row[2]).strip() if row[2] else ""
        stock_name = str(row[3]).strip() if row[3] else ""

        if not industry_code or not stock_code:
            continue

        symbol = stock_code.split(".")[0] if "." in stock_code else stock_code

        results.append(SwMemberRow(
            industry_code=industry_code,
            stock_code=stock_code,
            symbol=symbol,
            stock_name=stock_name,
        ))

    wb.close()
    logger.info("Parsed %d A-share SW industry member mappings", len(results))
    return results
