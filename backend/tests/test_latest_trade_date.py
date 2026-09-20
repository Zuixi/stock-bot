"""Tests for the shared latest-trade-date resolver + weekday heuristic (Task 2.4).

Task 2 rewired the market plane onto the completeness predicate
(:mod:`app.services.market_day_service`) so "latest day" has one source of truth.
Task 8 deleted the day-agnostic helpers (``get_latest_trade_date`` and fix round 1
also ``_latest_trade_date`` / ``_latest_trade_date_str``): all callers use the
resolver directly (so they can also surface ``as_of_quality``), and the ISO-string
Redis cache lives in the predicate module (covered by ``test_market_day_service.py``).
Only :func:`last_weekday` survives here — it is still used for the "is the displayed
day the latest expected trading day" label and for the empty-DB ``as_of`` fallback.
"""

from datetime import date

from app.services import market_service
from app.services.market_service import last_weekday


def test_last_weekday_friday_stays() -> None:
    assert last_weekday(date(2026, 9, 11)) == date(2026, 9, 11)  # 周五


def test_last_weekday_weekend_falls_back_to_friday() -> None:
    assert last_weekday(date(2026, 9, 12)) == date(2026, 9, 11)  # 周六
    assert last_weekday(date(2026, 9, 13)) == date(2026, 9, 11)  # 周日


def test_get_latest_trade_date_is_gone() -> None:
    """Task 8: the day-agnostic ``get_latest_trade_date`` helper was deleted.

    Its two former callers (``get_rankings`` / ``get_sw_industry_performance``) call
    the completeness resolver themselves, and a resurrected helper would invite a
    day-agnostic cache key back in.

    Fix round 1 also deleted the last survivors, ``_latest_trade_date`` (uncached
    delegate) and ``_latest_trade_date_str`` (its only caller): nothing in ``app/``
    referenced either, so they were production-dead. A resurrected uncached delegate
    is exactly how a second "which day is it" path grows back.
    """
    assert not hasattr(market_service, "get_latest_trade_date")
    assert not hasattr(market_service, "_latest_trade_date")
    assert not hasattr(market_service, "_latest_trade_date_str")
