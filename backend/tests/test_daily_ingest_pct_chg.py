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
async def test_per_stock_daily_ingest_maps_pct_chg_and_pre_close(monkeypatch) -> None:
    """Second construction site (``ingest_daily_quotes_for_stock``) must also carry both.

    Guards against dropping the kwargs from only the per-stock path — the
    by-trade-date test above would stay green.
    """
    captured: list[SimpleNamespace] = []

    async def _upsert(_db, quotes):
        captured.extend(quotes)
        return len(quotes)

    monkeypatch.setattr("app.repositories.quote_repo.upsert_quotes", _upsert)

    df = pd.DataFrame(
        [
            {
                "ts_code": "600000.SH",
                "trade_date": "20260909",
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

    await service.ingest_daily_quotes_for_stock(
        AsyncMock(),
        stock_id=7,
        exchange="Shanghai_Stocks",
        symbol="600000",
        start_date=date(2026, 9, 1),
        end_date=date(2026, 9, 10),
    )

    assert len(captured) == 1
    assert captured[0].stock_id == 7
    assert captured[0].pct_chg == 5.0
    assert captured[0].pre_close == 10.0


@pytest.mark.asyncio
async def test_upsert_quotes_carries_pct_chg_in_values_and_on_conflict() -> None:
    """Both the INSERT column list and the ON CONFLICT SET must carry the columns.

    Asserting the whole SQL string is not enough: ``excluded.pre_close`` in the
    SET clause alone satisfies a naive ``"pre_close" in sql`` check, so dropping
    the VALUES-dict entry would still "pass" while the INSERT path writes NULL.
    Split on ``ON CONFLICT`` and check the two halves separately.
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
    assert "ON CONFLICT" in sql
    insert_sql, conflict_sql = sql.split("ON CONFLICT", 1)
    # INSERT column list must include the columns themselves...
    assert "pre_close" in insert_sql
    assert "pct_chg" in insert_sql
    # ...and the conflict path must overwrite from the incoming (excluded) values.
    assert "pre_close = excluded.pre_close" in conflict_sql
    assert "pct_chg = excluded.pct_chg" in conflict_sql


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
