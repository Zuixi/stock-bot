"""Rankings whitelist / schema contract (Task 2.5) + cache behaviour (ruling S/Q)."""

from datetime import date

import pytest

from app.schemas.ranking import RankingResponseOut
from app.services import market_service
from app.services.market_service import _RANKING_ORDER


def test_ranking_type_whitelist() -> None:
    assert set(_RANKING_ORDER) == {"gainers", "losers", "amount", "turnover_rate", "volume"}
    assert _RANKING_ORDER["gainers"] == ("pct_chg", "DESC")
    assert _RANKING_ORDER["losers"] == ("pct_chg", "ASC")


def test_ranking_response_schema_roundtrip() -> None:
    out = RankingResponseOut(
        as_of="2026-09-10", is_latest_trading_day=True, type="gainers", items=[]
    )
    assert out.model_dump(mode="json")["as_of"] == "2026-09-10"


def test_quote_rank_sql_covers_exactly_the_quote_types() -> None:
    # turnover_rate reads daily_basic_indicators; the other four ride daily_quotes.
    assert set(market_service._QUOTE_RANK_SQL) == {"gainers", "losers", "amount", "volume"}


def test_quote_rank_sql_filters_null_pct_chg() -> None:
    # Ruling P: without this the 涨幅榜 leads with NULL rows (NULLS FIRST default).
    for stmt in market_service._QUOTE_RANK_SQL.values():
        assert "pct_chg IS NOT NULL" in str(stmt)


def test_quote_rank_sql_has_stock_id_tiebreak() -> None:
    # Deterministic order: equal sort values must not shuffle between identical calls.
    for stmt in market_service._QUOTE_RANK_SQL.values():
        assert str(stmt).count("q.stock_id ASC") == 1  # inner top-N
        assert str(stmt).count("t.stock_id ASC") == 1  # outer re-imposed order


def test_turnover_rank_sql_has_stock_id_tiebreak() -> None:
    sql = str(market_service._TURNOVER_RANK_SQL)
    assert "b.stock_id ASC" in sql
    assert "t.stock_id ASC" in sql


class RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))


class _Rows:
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def mappings(self) -> "_Rows":
        return self

    def all(self) -> list[dict]:
        return self._rows


class _FakeDb:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows
        self.calls: list[tuple[str, dict | None]] = []

    async def execute(self, stmt: object, params: dict | None = None) -> _Rows:
        self.calls.append((str(stmt), params))
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_get_rankings_cache_hit_skips_db(monkeypatch) -> None:
    """Cache key carries the resolved day; a hit resolves the day but runs no ranking SQL."""
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    cached = RankingResponseOut(
        as_of=date(2026, 9, 10),
        is_latest_trading_day=True,
        type="gainers",
        items=[],
    )
    cache.store["market:rankings:gainers:20:2026-09-10"] = cached.model_dump(mode="json")
    db = _FakeDb([])

    out = await market_service.get_rankings(db, cache, "gainers", 20)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 10)
    assert out.type == "gainers"
    assert db.calls == []


def _market_day(quality: str = "complete") -> object:
    return market_service.market_day_service.MarketDay(
        date(2026, 9, 10), quality, None, 5485, 5513, 1.0, True
    )


def _patch_resolved_day(monkeypatch) -> None:
    async def _resolve(_db, *, cache=None):
        return _market_day()

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)


@pytest.mark.asyncio
async def test_get_rankings_computes_and_sets_cache(monkeypatch) -> None:
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    db = _FakeDb(
        [
            {
                "symbol": "600000",
                "name": "浦发银行",
                "exchange": "Shanghai_Stocks",
                "close": 10.0,
                "pct_chg": 9.99,
                "amount": 1e9,
                "volume": 100000,
                "turnover_rate": 1.5,
                "total_mv": 1e6,
            }
        ]
    )

    out = await market_service.get_rankings(db, cache, "gainers", 5)  # type: ignore[arg-type]

    assert [i.symbol for i in out.items] == ["600000"]
    assert out.as_of == date(2026, 9, 10)
    assert out.as_of_quality == "complete"
    # Ruling Q: cache payload is JSON-mode (as_of is a string).
    assert cache.set_calls[0][0] == "market:rankings:gainers:5:2026-09-10"
    assert cache.set_calls[0][1]["as_of"] == "2026-09-10"  # type: ignore[index]
    assert cache.set_calls[0][1]["as_of_quality"] == "complete"  # type: ignore[index]
    assert cache.set_calls[0][2] == market_service._MARKET_CACHE_TTL


@pytest.mark.asyncio
async def test_get_rankings_rejects_unknown_type() -> None:
    with pytest.raises(ValueError, match="unknown ranking type"):
        await market_service.get_rankings(_FakeDb([]), None, "amplitude", 5)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_rankings_degrades_on_empty_db(monkeypatch) -> None:
    """Empty daily_quotes must return an empty payload, not propagate an error (500)."""

    async def _empty(_db, *, cache=None):
        return None

    # Production labels ``as_of`` from the Shanghai wall clock (``_today_sh()``),
    # not the host local date; pin it so the assertion cannot flake on a host
    # whose date lags/leads Asia/Shanghai (e.g. a UTC CI runner after 16:00 UTC).
    # Pinned to a Saturday: the expected label is Friday, proving both the
    # Shanghai-day source and the ``last_weekday`` roll-back are exercised.
    monkeypatch.setattr(market_service, "_today_sh", lambda: date(2026, 9, 19))
    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _empty)
    cache = RecordingCache()
    db = _FakeDb([])

    out = await market_service.get_rankings(db, cache, "gainers", 10)  # type: ignore[arg-type]

    assert out.items == []
    assert out.is_latest_trading_day is False
    assert out.as_of == date(2026, 9, 18), "周六的最近预期交易日是周五（上海时区）"
    assert out.as_of == market_service.last_weekday(market_service._today_sh())
    assert out.as_of_quality == "partial"
    assert db.calls == []
    assert cache.set_calls == [], "empty fallback must not be cached (recover immediately)"


@pytest.mark.asyncio
async def test_latest_trade_date_helper_delegates_uncached(monkeypatch) -> None:
    """The uncached thin delegate returns the resolved day (no cache dependency)."""
    seen: list[object] = []

    async def _fake(_db, *, cache=None):
        seen.append(cache)
        return _market_day()

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _fake)
    assert await market_service._latest_trade_date(_FakeDb([])) == date(2026, 9, 10)  # type: ignore[arg-type]
    assert seen == [None], "siblings must not acquire a cache dependency"


@pytest.mark.asyncio
async def test_latest_trade_date_helper_returns_none_on_empty(monkeypatch) -> None:
    async def _empty(_db, *, cache=None):
        return None

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _empty)
    assert await market_service._latest_trade_date(_FakeDb([])) is None  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_get_rankings_legacy_cache_payload_without_quality_reads_partial(
    monkeypatch,
) -> None:
    """Cache payloads without ``as_of_quality`` must not be read as ``"complete"``.

    The schema default (``"complete"``) would dress a stale payload up as a fresh
    complete day for up to ``_MARKET_CACHE_TTL``; an absent key means ``"partial"``.
    """
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    legacy = RankingResponseOut(
        as_of=date(2026, 9, 10), is_latest_trading_day=True, type="gainers", items=[]
    ).model_dump(mode="json")
    del legacy["as_of_quality"]  # legacy shape: key absent, not null
    cache.store["market:rankings:gainers:20:2026-09-10"] = legacy
    db = _FakeDb([])

    out = await market_service.get_rankings(db, cache, "gainers", 20)  # type: ignore[arg-type]

    assert out.as_of_quality == "partial"
    assert out.as_of == date(2026, 9, 10)
    assert db.calls == []


@pytest.mark.asyncio
async def test_get_rankings_does_not_reuse_another_days_payload(monkeypatch) -> None:
    """Two poisons: the pre-Task-8 day-less key and the previous day's key.

    A day-agnostic lookup would hit one of them and replay ``as_of=2026-09-09``; a
    day-scoped key must miss both, run the ranking SQL for 2026-09-10 and write the
    day-scoped key.
    """
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    for key in ("market:rankings:gainers:20", "market:rankings:gainers:20:2026-09-09"):
        payload = RankingResponseOut(
            as_of=date(2026, 9, 9), is_latest_trading_day=True, type="gainers", items=[]
        ).model_dump(mode="json")
        # wrong shape on purpose: a poisoned hit would blow up / return it
        payload["items"] = [{"symbol": "STALE"}]
        cache.store[key] = payload
    db = _FakeDb([])

    out = await market_service.get_rankings(db, cache, "gainers", 20)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 10)
    assert out.items == []
    assert len(db.calls) == 1, "the ranking SQL must run for the resolved day"
    assert cache.set_calls[0][0] == "market:rankings:gainers:20:2026-09-10"
