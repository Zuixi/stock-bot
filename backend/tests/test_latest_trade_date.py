"""Tests for the shared latest-trade-date resolver + weekday heuristic (Task 2.4).

Task 2 rewired both helpers onto the completeness predicate
(:mod:`app.services.market_day_service`) so "latest day" has one source of truth.
The ISO-string Redis cache these tests used to assert now lives in the predicate
module (covered by ``test_market_day_service.py``); here we lock the delegation +
``ValueError`` contract kept for the two legacy callers.
"""

from datetime import date

import pytest

from app.services import market_service
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


class _FakeDb:
    def __init__(self) -> None:
        self.execute_calls = 0


def _patch_resolver(monkeypatch: pytest.MonkeyPatch, md: object) -> list[dict[str, object]]:
    """Patch the predicate seam on its call site in ``market_service``."""
    seen: list[dict[str, object]] = []

    async def _resolve(_db: object, *, cache: object = None) -> object:
        seen.append({"cache": cache})
        return md

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)
    return seen


def _market_day(day: date = date(2026, 9, 10)) -> object:
    return market_service.market_day_service.MarketDay(day, "complete", None, 5485, 5513, 1.0, True)


@pytest.mark.asyncio
async def test_get_latest_trade_date_returns_resolved_day(monkeypatch) -> None:
    _patch_resolver(monkeypatch, _market_day())
    out = await get_latest_trade_date(_FakeDb())  # type: ignore[arg-type]
    assert out == date(2026, 9, 10)


@pytest.mark.asyncio
async def test_get_latest_trade_date_forwards_cache(monkeypatch) -> None:
    seen = _patch_resolver(monkeypatch, _market_day())
    cache = RecordingCache()
    await get_latest_trade_date(_FakeDb(), cache)  # type: ignore[arg-type]
    assert seen == [{"cache": cache}]


@pytest.mark.asyncio
async def test_get_latest_trade_date_empty_table_raises(monkeypatch) -> None:
    _patch_resolver(monkeypatch, None)
    with pytest.raises(ValueError, match="daily_quotes is empty"):
        await get_latest_trade_date(_FakeDb())  # type: ignore[arg-type]
