"""SW L1 industry performance aggregation contract (Task 3.1).

The rollup walks L3 -> L2 -> L1 via ``parent_code`` and aggregates only stocks that
actually have a quote (non-null ``pct_chg``) on the latest trade date.
"""

from datetime import date

import pytest

from app.schemas.sw_performance import SwPerformanceResponseOut
from app.services import market_service


def test_sw_perf_sql_rolls_up_l3_to_l1() -> None:
    sql = str(market_service._SW_PERF_SQL)
    assert "sw_industry_members" in sql
    assert "sw_industry_classes" in sql
    # two-hop parent walk: c3.parent_code -> c2, c2.parent_code -> c1
    assert sql.count("parent_code") == 2
    assert "level = 3" in sql


def test_sw_perf_sql_aggregates_only_quoted_stocks() -> None:
    sql = str(market_service._SW_PERF_SQL)
    # counts/averages ride the quote join, not raw membership
    assert "JOIN daily_quotes q ON q.stock_id = s.id" in sql
    assert "q.pct_chg IS NOT NULL" in sql
    assert "avg(q.pct_chg)" in sql
    assert "sum(q.amount)" in sql
    assert "count(*) FILTER (WHERE q.pct_chg > 0)" in sql
    assert "count(*) FILTER (WHERE q.pct_chg < 0)" in sql
    assert "GROUP BY lm.code, lm.name" in sql
    assert "ORDER BY avg_pct_chg DESC" in sql


def test_sw_performance_schema_roundtrip() -> None:
    out = SwPerformanceResponseOut(
        as_of=date(2026, 9, 9),
        items=[
            {
                "code": "110000",
                "name": "农林牧渔",
                "member_count": 10,
                "avg_pct_chg": 1.23,
                "total_amount": 1000.0,
                "up_count": 6,
                "down_count": 4,
            }
        ],
    )
    dumped = out.model_dump(mode="json")
    assert dumped["as_of"] == "2026-09-09"
    assert dumped["items"][0]["code"] == "110000"


def test_sw_performance_total_amount_nullable() -> None:
    """``sum(amount)`` can be NULL; a non-optional field would 500 the anonymous endpoint."""
    out = SwPerformanceResponseOut(
        as_of=date(2026, 9, 9),
        items=[
            {
                "code": "110000",
                "name": "农林牧渔",
                "member_count": 10,
                "avg_pct_chg": 1.23,
                "total_amount": None,
                "up_count": 6,
                "down_count": 4,
            }
        ],
    )
    assert out.items[0].total_amount is None


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


def _row(code: str, name: str, avg: float = 1.0) -> dict:
    return {
        "code": code,
        "name": name,
        "member_count": 10,
        "avg_pct_chg": avg,
        "total_amount": 1000.0,
        "up_count": 6,
        "down_count": 4,
    }


@pytest.mark.asyncio
async def test_sw_performance_cache_hit_skips_db(monkeypatch) -> None:
    """Cache key carries the resolved day; a hit resolves the day but runs no rollup SQL."""
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    cached = SwPerformanceResponseOut(as_of=date(2026, 9, 9), items=[_row("110000", "农林牧渔")])
    cache.store["market:sw-performance:2026-09-09"] = cached.model_dump(mode="json")
    db = _FakeDb([])

    out = await market_service.get_sw_industry_performance(db, cache, 31)  # type: ignore[arg-type]

    assert out.items[0].code == "110000"
    assert db.calls == []


def _patch_resolved_day(monkeypatch, quality: str = "complete") -> None:
    async def _resolve(_db, *, cache=None):
        return market_service.market_day_service.MarketDay(
            date(2026, 9, 9), quality, None, 5485, 5513, 1.0, True
        )

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)


@pytest.mark.asyncio
async def test_sw_performance_computes_and_caches(monkeypatch) -> None:
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    db = _FakeDb([_row("110000", "农林牧渔", avg=1.23)])

    out = await market_service.get_sw_industry_performance(db, cache, 31)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 9)
    assert out.as_of_quality == "complete"
    assert out.items[0].avg_pct_chg == 1.23
    # Ruling U: anonymous endpoint must go through CacheClient with _MARKET_CACHE_TTL.
    assert cache.set_calls[0][0] == "market:sw-performance:2026-09-09"
    assert cache.set_calls[0][1]["as_of"] == "2026-09-09"  # type: ignore[index]
    assert cache.set_calls[0][1]["as_of_quality"] == "complete"  # type: ignore[index]
    assert cache.set_calls[0][2] == market_service._MARKET_CACHE_TTL


@pytest.mark.asyncio
async def test_sw_performance_limit_applied_on_cache_hit(monkeypatch) -> None:
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    cached = SwPerformanceResponseOut(
        as_of=date(2026, 9, 9),
        items=[_row("110000", "农林牧渔"), _row("220000", "基础化工"), _row("330000", "钢铁")],
    )
    cache.store["market:sw-performance:2026-09-09"] = cached.model_dump(mode="json")

    out = await market_service.get_sw_industry_performance(_FakeDb([]), cache, 2)  # type: ignore[arg-type]

    assert [i.code for i in out.items] == ["110000", "220000"]


@pytest.mark.asyncio
async def test_sw_performance_does_not_reuse_another_days_payload(monkeypatch) -> None:
    """Day-less and previous-day keys are both poisoned; only a day-scoped key misses."""
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    stale = SwPerformanceResponseOut(as_of=date(2026, 9, 8), items=[_row("999999", "旧日")])
    cache.store["market:sw-performance"] = stale.model_dump(mode="json")
    cache.store["market:sw-performance:2026-09-08"] = stale.model_dump(mode="json")
    db = _FakeDb([_row("110000", "农林牧渔", avg=1.23)])

    out = await market_service.get_sw_industry_performance(db, cache, 31)  # type: ignore[arg-type]

    assert [i.code for i in out.items] == ["110000"]
    assert len(db.calls) == 1, "the rollup SQL must run for the resolved day"
    assert cache.set_calls[0][0] == "market:sw-performance:2026-09-09"


@pytest.mark.asyncio
async def test_sw_performance_degrades_on_empty_db(monkeypatch) -> None:
    """Public homepage block: empty daily_quotes must degrade, never 500, and not cache.

    The label must come from the **Shanghai** wall clock, same as the non-empty branch
    and as ``get_rankings`` (fix round 1, Minor 10): a host-local ``date.today()``
    mislabels the rollover window. ``date`` is pinned to a Sunday and ``_today_sh`` to
    a Saturday, so the two sources produce different Fridays (09-04 vs 09-18) and the
    assertion can only pass on the Shanghai one.
    """

    class _PinnedDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)  # 宿主本地"今天"（周日）

    async def _empty(_db, *, cache=None):
        return None

    monkeypatch.setattr(market_service, "date", _PinnedDate)
    monkeypatch.setattr(market_service, "_today_sh", lambda: date(2026, 9, 19))  # 周六
    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _empty)
    cache = RecordingCache()

    out = await market_service.get_sw_industry_performance(_FakeDb([]), cache, 31)  # type: ignore[arg-type]

    assert out.items == []
    assert out.as_of == date(2026, 9, 18), "周六的最近预期交易日是周五（上海时区）"
    assert out.as_of == market_service.last_weekday(market_service._today_sh())
    assert out.as_of_quality == "partial"
    assert cache.set_calls == []


@pytest.mark.asyncio
async def test_sw_performance_legacy_cache_payload_without_quality_reads_partial(
    monkeypatch,
) -> None:
    """Payloads lacking ``as_of_quality``; the schema default ("complete") is a lie.

    Absent key => "partial" (quality unknown), never the optimistic default.
    """
    _patch_resolved_day(monkeypatch)
    cache = RecordingCache()
    legacy = SwPerformanceResponseOut(
        as_of=date(2026, 9, 9), items=[_row("110000", "农林牧渔")]
    ).model_dump(mode="json")
    del legacy["as_of_quality"]  # legacy shape: key absent, not null
    cache.store["market:sw-performance:2026-09-09"] = legacy

    out = await market_service.get_sw_industry_performance(_FakeDb([]), cache, 31)  # type: ignore[arg-type]

    assert out.as_of_quality == "partial"
    assert out.items[0].code == "110000"
