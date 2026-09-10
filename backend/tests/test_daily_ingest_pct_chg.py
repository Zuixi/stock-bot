"""pct_chg/pre_close must survive the ingest mapping (TuShare daily returns them natively)."""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pandas as pd
import pytest
from sqlalchemy.dialects import postgresql

from app.models.quote import DailyQuote
from app.repositories import quote_repo
from app.services.tushare_ingest import TuShareIngestService


@pytest.mark.asyncio
async def test_daily_ingest_maps_pct_chg_and_pre_close(monkeypatch) -> None:
    """Feed a daily row carrying pre_close/pct_chg; the persisted object must carry both.

    ``ingest_daily_quotes`` fetches internally, so the test stubs the client's
    ``fetch_daily`` rather than calling a non-existent ``ingest_daily(df=...)``.
    """
    captured: list[SimpleNamespace] = []

    async def _upsert(_db, quotes):
        captured.extend(quotes)
        return len(quotes)

    async def _stock_id_map(_self, _db):
        return {"600000.SH": 1}

    monkeypatch.setattr("app.repositories.quote_repo.upsert_quotes", _upsert)
    monkeypatch.setattr(TuShareIngestService, "_build_stock_id_map", _stock_id_map, raising=True)

    df = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "trade_date": "20260910",
                "open": 10.0,
                "high": 11.0,
                "low": 9.9,
                "close": 10.5,
                "pre_close": 10.0,
                "change": 0.5,
                "pct_chg": 5.0,
                "vol": 1000.0,
                "amount": 10500.0,
            },
        ]
    )

    client = AsyncMock()
    client.fetch_daily = AsyncMock(return_value=df)
    service = TuShareIngestService(client=client, data_saver=AsyncMock())

    await service.ingest_daily_quotes(AsyncMock(), trade_date="20260910")

    assert len(captured) == 1
    assert captured[0].pct_chg == 5.0
    assert captured[0].pre_close == 10.0
    # Existing OHLCV mapping must be untouched by the new fields.
    assert captured[0].close == 10.5
    assert captured[0].amount == 10500.0


@pytest.mark.asyncio
async def test_upsert_quotes_carries_pct_chg_in_values_and_on_conflict() -> None:
    """Both the insert values and the ON CONFLICT SET must carry the new columns.

    Missing either one drops the field silently: a plain column omission on
    INSERT, or a no-op re-ingest clobbering an existing value on conflict.
    """
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=1))

    quote = DailyQuote(
        stock_id=1,
        trade_date=date(2026, 9, 10),
        close=10.5,
        pre_close=10.0,
        pct_chg=5.0,
    )
    await quote_repo.upsert_quotes(db, [quote])

    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "pre_close" in sql
    assert "pct_chg" in sql
    # The SET side uses excluded.*; without it the conflict path keeps the old NULL.
    assert "pre_close = excluded.pre_close" in sql
    assert "pct_chg = excluded.pct_chg" in sql


@pytest.mark.asyncio
async def test_update_pct_chg_for_date_is_update_only() -> None:
    """Backfill must reconcile existing rows: UPDATE, never INSERT, no LAG derivation."""
    db = AsyncMock()
    db.execute = AsyncMock(return_value=SimpleNamespace(rowcount=2))

    updated = await quote_repo.update_pct_chg_for_date(
        db, date(2026, 9, 10), [(1, 10.0, 5.0), (2, 20.0, -1.5)]
    )

    assert updated == 2
    stmt = db.execute.await_args.args[0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert sql.lstrip().upper().startswith("UPDATE")
    assert "pre_close" in sql and "pct_chg" in sql
    assert "INSERT" not in sql.upper()
    assert "LAG" not in sql.upper()
    # One VALUES-join statement, not per-row executemany (asyncpg returns -1 there).
    assert "VALUES" in sql.upper()

    empty_db = AsyncMock()
    assert await quote_repo.update_pct_chg_for_date(empty_db, date(2026, 9, 10), []) == 0
    empty_db.execute.assert_not_awaited()
