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
async def test_sw_performance_cache_hit_skips_db() -> None:
    cache = RecordingCache()
    cached = SwPerformanceResponseOut(as_of=date(2026, 9, 9), items=[_row("110000", "农林牧渔")])
    cache.store["market:sw-performance"] = cached.model_dump(mode="json")
    db = _FakeDb([])

    out = await market_service.get_sw_industry_performance(db, cache, 31)  # type: ignore[arg-type]

    assert out.items[0].code == "110000"
    assert db.calls == []


@pytest.mark.asyncio
async def test_sw_performance_computes_and_caches(monkeypatch) -> None:
    async def _fake_latest(_db, _cache=None):
        return date(2026, 9, 9)

    monkeypatch.setattr(market_service, "get_latest_trade_date", _fake_latest)
    cache = RecordingCache()
    db = _FakeDb([_row("110000", "农林牧渔", avg=1.23)])

    out = await market_service.get_sw_industry_performance(db, cache, 31)  # type: ignore[arg-type]

    assert out.as_of == date(2026, 9, 9)
    assert out.items[0].avg_pct_chg == 1.23
    # Ruling U: anonymous endpoint must go through CacheClient with _MARKET_CACHE_TTL.
    assert cache.set_calls[0][0] == "market:sw-performance"
    assert cache.set_calls[0][1]["as_of"] == "2026-09-09"  # type: ignore[index]
    assert cache.set_calls[0][2] == market_service._MARKET_CACHE_TTL


@pytest.mark.asyncio
async def test_sw_performance_limit_applied_on_cache_hit() -> None:
    cache = RecordingCache()
    cached = SwPerformanceResponseOut(
        as_of=date(2026, 9, 9),
        items=[_row("110000", "农林牧渔"), _row("220000", "基础化工"), _row("330000", "钢铁")],
    )
    cache.store["market:sw-performance"] = cached.model_dump(mode="json")

    out = await market_service.get_sw_industry_performance(_FakeDb([]), cache, 2)  # type: ignore[arg-type]

    assert [i.code for i in out.items] == ["110000", "220000"]


@pytest.mark.asyncio
async def test_sw_performance_degrades_on_empty_db(monkeypatch) -> None:
    """Public homepage block: empty daily_quotes must degrade, never 500, and not cache."""

    async def _empty(_db, _cache=None):
        raise ValueError("daily_quotes is empty — run ingest first")

    monkeypatch.setattr(market_service, "get_latest_trade_date", _empty)
    cache = RecordingCache()

    out = await market_service.get_sw_industry_performance(_FakeDb([]), cache, 31)  # type: ignore[arg-type]

    assert out.items == []
    assert out.as_of == market_service.last_weekday(date.today())
    assert cache.set_calls == []
