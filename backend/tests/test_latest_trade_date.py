"""Tests for the shared latest-trade-date resolver + weekday heuristic (Task 2.4)."""

from datetime import date

import pytest

from app.services.market_service import get_latest_trade_date, last_weekday


def test_last_weekday_friday_stays() -> None:
    assert last_weekday(date(2026, 9, 11)) == date(2026, 9, 11)  # 周五


def test_last_weekday_weekend_falls_back_to_friday() -> None:
    assert last_weekday(date(2026, 9, 12)) == date(2026, 9, 11)  # 周六
    assert last_weekday(date(2026, 9, 13)) == date(2026, 9, 11)  # 周日


class RecordingCache:
    """CacheClient double: get/set contract matches app.core.redis.CacheClient."""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.set_calls: list[tuple[str, object, int | None]] = []

    async def get(self, key: str):
        return self.store.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.store[key] = value
        self.set_calls.append((key, value, ttl))


class _FakeResult:
    def __init__(self, value: object) -> None:
        self._value = value

    def scalar_one_or_none(self) -> object:
        return self._value


class _FakeDb:
    def __init__(self, value: object) -> None:
        self.value = value
        self.execute_calls = 0

    async def execute(self, *args: object, **kwargs: object) -> _FakeResult:
        self.execute_calls += 1
        return _FakeResult(self.value)


@pytest.mark.asyncio
async def test_get_latest_trade_date_reads_db_then_caches_isoformat() -> None:
    cache = RecordingCache()
    db = _FakeDb(date(2026, 9, 10))

    out = await get_latest_trade_date(db, cache)  # type: ignore[arg-type]

    assert out == date(2026, 9, 10)
    # Ruling Q: the cache must hold a JSON-serializable ISO string, not a date.
    assert cache.store["market:latest_trade_date"] == "2026-09-10"
    assert cache.set_calls == [("market:latest_trade_date", "2026-09-10", 300)]


@pytest.mark.asyncio
async def test_get_latest_trade_date_cache_hit_skips_db() -> None:
    cache = RecordingCache()
    cache.store["market:latest_trade_date"] = "2026-09-10"
    db = _FakeDb(None)

    out = await get_latest_trade_date(db, cache)  # type: ignore[arg-type]

    assert out == date(2026, 9, 10)
    assert db.execute_calls == 0


@pytest.mark.asyncio
async def test_get_latest_trade_date_non_str_cache_falls_through_to_db() -> None:
    """A non-string cached value must not be ``cast`` through as a ``date``.

    ``cast`` is a runtime no-op, so trusting a non-str payload would leak e.g. an
    int/None/float to callers that then ``.strftime`` or compare it as a date.
    """
    cache = RecordingCache()
    cache.store["market:latest_trade_date"] = 1778544000  # int payload, not a date
    db = _FakeDb(date(2026, 9, 10))

    out = await get_latest_trade_date(db, cache)  # type: ignore[arg-type]

    assert out == date(2026, 9, 10)
    assert db.execute_calls == 1
    # re-resolved value overwrites the bad cache entry
    assert cache.store["market:latest_trade_date"] == "2026-09-10"


@pytest.mark.asyncio
async def test_get_latest_trade_date_malformed_str_cache_falls_through_to_db() -> None:
    cache = RecordingCache()
    cache.store["market:latest_trade_date"] = "not-a-date"
    db = _FakeDb(date(2026, 9, 10))

    out = await get_latest_trade_date(db, cache)  # type: ignore[arg-type]

    assert out == date(2026, 9, 10)
    assert db.execute_calls == 1
    assert cache.store["market:latest_trade_date"] == "2026-09-10"


@pytest.mark.asyncio
async def test_get_latest_trade_date_empty_table_raises() -> None:
    db = _FakeDb(None)

    with pytest.raises(ValueError, match="daily_quotes is empty"):
        await get_latest_trade_date(db)  # type: ignore[arg-type]
