"""Tests for universe ingest helper logic."""

from datetime import date

from app.services.universe_ingest import (
    merge_detail_into_record,
    normalize_exchange,
    parse_listing_date,
)


def test_normalize_exchange() -> None:
    assert normalize_exchange("sse") == ("sse", "Shanghai_Stocks")
    assert normalize_exchange("Shenzen_Stocks") == ("szse", "Shenzen_Stocks")


def test_parse_listing_date() -> None:
    assert parse_listing_date("20260412") == date(2026, 4, 12)
    assert parse_listing_date("2026-04-12") == date(2026, 4, 12)
    assert parse_listing_date("2026/04/12") == date(2026, 4, 12)
    assert parse_listing_date("unknown") is None


def test_merge_detail_into_record() -> None:
    base = {
        "exchange": "Shanghai_Stocks",
        "symbol": "600519",
        "name": "贵州茅台",
        "category": "主板A股",
        "full_name": None,
    }
    detail = {"公司名称": "贵州茅台酒股份有限公司", "行业": "食品饮料", "provider": "tushare"}
    merged = merge_detail_into_record(base, detail)
    assert merged["detail"]["provider"] == "tushare"


def test_merge_detail_none() -> None:
    base = {"exchange": "Shanghai_Stocks", "symbol": "600519"}
    merged = merge_detail_into_record(base, None)
    assert merged == base
