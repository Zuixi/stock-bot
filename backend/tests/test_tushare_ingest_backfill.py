"""Unit tests for startup 3-year daily backfill logic."""

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from app.services.tushare_ingest import TuShareIngestService


@pytest.mark.asyncio
async def test_list_stocks_missing_daily_coverage_skips_fully_covered(monkeypatch) -> None:
    service = TuShareIngestService(client=AsyncMock(), data_saver=AsyncMock())
    asof_date = date(2026, 4, 22)
    window_start = asof_date - timedelta(days=365 * 3)

    stock = SimpleNamespace(
        id=1,
        exchange="Shanghai_Stocks",
        symbol="600000",
        list_date=date(2000, 1, 1),
    )

    async def _list_all_stocks(_db):
        return [stock]

    async def _get_bounds(_db, stock_ids, start_date, end_date):
        assert stock_ids == [1]
        assert start_date == window_start
        assert end_date == asof_date
        return {1: (window_start + timedelta(days=2), asof_date - timedelta(days=3), 700)}

    monkeypatch.setattr("app.repositories.stock_repo.list_all_stocks", _list_all_stocks)
    monkeypatch.setattr(
        "app.repositories.quote_repo.get_trade_date_bounds_for_stocks",
        _get_bounds,
    )

    missing = await service.list_stocks_missing_daily_coverage(
        db=AsyncMock(),
        years=3,
        asof_date=asof_date,
        tolerance_days=10,
    )
    assert missing == []


@pytest.mark.asyncio
async def test_list_stocks_missing_daily_coverage_flags_gap(monkeypatch) -> None:
    service = TuShareIngestService(client=AsyncMock(), data_saver=AsyncMock())
    asof_date = date(2026, 4, 22)
    window_start = asof_date - timedelta(days=365 * 3)

    stock = SimpleNamespace(
        id=2,
        exchange="Shenzen_Stocks",
        symbol="000001",
        list_date=date(2005, 1, 1),
    )

    async def _list_all_stocks(_db):
        return [stock]

    async def _get_bounds(_db, stock_ids, start_date, end_date):
        assert stock_ids == [2]
        assert start_date == window_start
        assert end_date == asof_date
        return {2: (window_start + timedelta(days=40), asof_date - timedelta(days=1), 500)}

    monkeypatch.setattr("app.repositories.stock_repo.list_all_stocks", _list_all_stocks)
    monkeypatch.setattr(
        "app.repositories.quote_repo.get_trade_date_bounds_for_stocks",
        _get_bounds,
    )

    missing = await service.list_stocks_missing_daily_coverage(
        db=AsyncMock(),
        years=3,
        asof_date=asof_date,
        tolerance_days=10,
    )
    assert len(missing) == 1
    assert missing[0]["stock_id"] == 2
    assert missing[0]["reason"] == "gap"


@pytest.mark.asyncio
async def test_list_stocks_missing_daily_coverage_respects_recent_listing(monkeypatch) -> None:
    service = TuShareIngestService(client=AsyncMock(), data_saver=AsyncMock())
    asof_date = date(2026, 4, 22)

    stock = SimpleNamespace(
        id=3,
        exchange="Beijing_Stocks",
        symbol="430001",
        list_date=date(2025, 1, 1),
    )

    async def _list_all_stocks(_db):
        return [stock]

    async def _get_bounds(_db, stock_ids, start_date, end_date):
        assert stock_ids == [3]
        assert start_date <= stock.list_date
        assert end_date == asof_date
        return {3: (date(2025, 1, 3), asof_date - timedelta(days=2), 200)}

    monkeypatch.setattr("app.repositories.stock_repo.list_all_stocks", _list_all_stocks)
    monkeypatch.setattr(
        "app.repositories.quote_repo.get_trade_date_bounds_for_stocks",
        _get_bounds,
    )

    missing = await service.list_stocks_missing_daily_coverage(
        db=AsyncMock(),
        years=3,
        asof_date=asof_date,
        tolerance_days=10,
    )
    assert missing == []


@pytest.mark.asyncio
async def test_ingest_daily_quotes_for_stock_upserts_rows(monkeypatch) -> None:
    client = AsyncMock()
    client.fetch_daily.return_value = pd.DataFrame(
        [
            {
                "trade_date": "20260422",
                "open": 10.0,
                "high": 10.5,
                "low": 9.8,
                "close": 10.2,
                "vol": 1000,
                "amount": 5000,
            },
            {
                "trade_date": "20260421",
                "open": 10.1,
                "high": 10.6,
                "low": 9.9,
                "close": 10.0,
                "vol": 1200,
                "amount": 5100,
            },
        ]
    )
    service = TuShareIngestService(client=client, data_saver=AsyncMock())

    async def _upsert_quotes(_db, quotes):
        assert len(quotes) == 2
        return len(quotes)

    monkeypatch.setattr("app.repositories.quote_repo.upsert_quotes", _upsert_quotes)

    result = await service.ingest_daily_quotes_for_stock(
        db=AsyncMock(),
        stock_id=10,
        exchange="Shanghai_Stocks",
        symbol="600000",
        start_date=date(2023, 4, 22),
        end_date=date(2026, 4, 22),
        save_raw=False,
    )

    assert result["upserted"] == 2
    assert result["saved"] == 2
    client.fetch_daily.assert_awaited_once()
