"""Tests for universe ingest helper logic."""

from datetime import date

import pytest

from app.services.universe_ingest import (
    UniverseDataProvider,
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
    detail = {"公司名称": "贵州茅台酒股份有限公司", "行业": "食品饮料", "provider": "akshare"}
    merged = merge_detail_into_record(base, detail)
    assert merged["full_name"] == "贵州茅台酒股份有限公司"
    assert merged["csrc_desc"] == "食品饮料"
    assert merged["detail"]["provider"] == "akshare"


@pytest.mark.asyncio
async def test_fetch_universe_auto_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = UniverseDataProvider()

    async def fake_crawler(*_args, **_kwargs):
        return []

    async def fake_akshare(*_args, **_kwargs):
        return [
            {
                "exchange": "Shanghai_Stocks",
                "symbol": "600000",
                "name": "浦发银行",
                "category": "Shanghai_Stocks",
            }
        ]

    monkeypatch.setattr(provider, "_fetch_via_crawler", fake_crawler)
    monkeypatch.setattr(provider, "_fetch_via_akshare_universe", fake_akshare)

    records = await provider.fetch_universe_records("sse", source="auto")
    assert len(records) == 1
    assert records[0]["symbol"] == "600000"
