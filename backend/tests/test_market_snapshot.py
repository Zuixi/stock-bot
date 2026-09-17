"""``market_snapshot_service`` 契约：取行 SQL、分组/汇总纯函数、缓存 JSON 边界。

离线：``_fetch_rows`` 是 SQL seam，缓存用 JSON 边界替身，全程不碰库、不打网。
"""

import json
import re
from datetime import date
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.dialects import postgresql

from app.services import market_snapshot_service as mss

D16 = date(2026, 9, 16)
DAY_KEY = "market:day:rows:2026-09-16"


def _row(**overrides: Any) -> dict[str, Any]:
    """One snapshot row in the module's canonical (JSON-safe) shape."""
    row: dict[str, Any] = {
        "stock_id": 1,
        "symbol": "600000",
        "name": "浦发银行",
        "csrc_desc": "银行",
        "province": "上海",
        "close": 9.1,
        "pct_chg": -0.8715,
        "amount": 656348.14,
        "total_mv": 30308312.85,
        "circ_mv": 30308312.85,
        "turnover_rate": 0.2172,
    }
    row.update(overrides)
    return row


class _FakeCache:
    """Redis 的 JSON 边界替身：写入必须能 ``json.dumps``，读出必是反序列化结果。

    ``json.dumps`` 不带 ``default=``：往缓存里塞 Decimal/date 会当场 TypeError，
    于是「写出的 payload 是否 JSON 安全」是被测出来的，而不是被 ``default=str`` 掩盖。
    """

    def __init__(self, payload: Any = None) -> None:
        self._raw = None if payload is None else json.dumps(payload, ensure_ascii=False)
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        return None if self._raw is None else json.loads(self._raw)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._raw = json.dumps(value, ensure_ascii=False)  # raises on non JSON-safe values
        self.sets.append((key, value, ttl))


# ---------------------------------------------------------------------------
# SQL: stored pct_chg, no per-stock previous-close lookup
# ---------------------------------------------------------------------------


def test_snapshot_sql_reads_stored_pct_chg_and_has_no_prev_close_lateral() -> None:
    """One statement, one LATERAL (daily_basic only), pct_chg straight off the row.

    The old shape ran ``LEFT JOIN LATERAL (… trade_date < :day …)`` per stock to
    recompute pct_chg. Three independent things must be gone/in place, so a
    partial regression (e.g. keeping the lateral but also selecting pct_chg)
    still fails.
    """
    sql = str(mss._snapshot_stmt(D16).compile(dialect=postgresql.dialect()))

    # 1. the stored column is selected, not recomputed from close/pre_close
    assert "daily_quotes.pct_chg" in sql
    assert "CASE" not in sql.upper()

    # 2. no "trade_date < day" previous-close lookup anywhere
    assert re.search(r"trade_date\s*<\s*(?!=)", sql) is None

    # 3. exactly one LATERAL, over daily_basic_indicators, and daily_quotes is
    #    read exactly once (a prev-close lateral would add a second FROM)
    assert sql.count("LATERAL") == 1
    assert "daily_basic_indicators" in sql.split("LATERAL", 1)[1]
    assert sql.count("FROM daily_quotes") == 1

    # ordered so downstream grouping/slicing is deterministic
    assert "ORDER BY daily_quotes.stock_id" in sql


def test_snapshot_stmt_binds_the_requested_day_for_both_tables() -> None:
    """The day must reach daily_quotes (the row filter) and daily_basic (the cap cut-off)."""
    sql = str(mss._snapshot_stmt(D16).compile(dialect=postgresql.dialect()))
    assert "daily_quotes.trade_date = %(day)s" in sql
    assert "daily_basic_indicators.trade_date <= %(day)s" in sql


# ---------------------------------------------------------------------------
# group_by
# ---------------------------------------------------------------------------


def test_group_by_skips_none_and_empty_keys_and_preserves_row_order() -> None:
    rows = [
        _row(stock_id=3, csrc_desc="银行"),
        _row(stock_id=1, csrc_desc=None),
        _row(stock_id=2, csrc_desc=""),
        _row(stock_id=4, csrc_desc="医药"),
        _row(stock_id=5, csrc_desc="银行"),
    ]

    groups = mss.group_by(rows, "csrc_desc")

    # skipped keys produce no bucket at all ("None"/"" must not show up as groups)
    assert list(groups) == ["银行", "医药"]
    assert [r["stock_id"] for r in groups["银行"]] == [3, 5]
    assert [r["stock_id"] for r in groups["医药"]] == [4]
    assert sum(len(items) for items in groups.values()) == 3


def test_group_by_returns_empty_dict_for_empty_input() -> None:
    assert mss.group_by([], "csrc_desc") == {}


# ---------------------------------------------------------------------------
# summarize_group
# ---------------------------------------------------------------------------


def test_summarize_group_all_five_fields_with_none_counted_as_flat() -> None:
    """``None`` pct_chg is flat; the mean is over non-null values only."""
    items = [
        _row(pct_chg=3.0),
        _row(pct_chg=0.0),
        _row(pct_chg=None),
        _row(pct_chg=-1.5),
    ]

    out = mss.summarize_group(items)

    assert out["total"] == 4
    assert out["up_count"] == 1
    assert out["flat_count"] == 2  # 0.0 and None
    assert out["down_count"] == 1
    # (3.0 + 0.0 + (-1.5)) / 3 = 0.5 — the None must NOT drag it to 0.375
    assert out["avg_chg"] == pytest.approx(0.5)
    # the counts partition the group (old SQL's ELSE 0 fallback semantics)
    assert out["up_count"] + out["flat_count"] + out["down_count"] == out["total"]


def test_summarize_group_empty_input_is_all_zero() -> None:
    assert mss.summarize_group([]) == {
        "total": 0,
        "up_count": 0,
        "flat_count": 0,
        "down_count": 0,
        "avg_chg": 0.0,
    }


def test_summarize_group_all_null_pct_chg_has_zero_mean() -> None:
    """No non-null pct_chg → 0.0 (old SQL AVG(...) → NULL → ``or 0``)."""
    out = mss.summarize_group([_row(pct_chg=None), _row(pct_chg=None)])
    assert out == {
        "total": 2,
        "up_count": 0,
        "flat_count": 2,
        "down_count": 0,
        "avg_chg": 0.0,
    }


# ---------------------------------------------------------------------------
# cache round-trip
# ---------------------------------------------------------------------------


def test_payload_round_trips_decimals_through_json() -> None:
    """DB Decimals become floats on write; the cached bytes rebuild the same rows."""
    db_rows = [
        _row(close=Decimal("9.1000"), pct_chg=Decimal("-0.8715"), amount=None),
    ]

    payload = mss._to_payload(db_rows)

    assert payload == [_row(close=9.1, pct_chg=-0.8715, amount=None)]
    assert isinstance(payload[0]["close"], float)
    cached = json.loads(json.dumps(payload))  # the real CacheClient boundary
    assert mss._from_payload(cached) == payload


async def test_cache_hit_short_circuits_the_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hit returns cached rows, never calls the DB seam, and never rewrites the entry."""
    fetch = AsyncMock(return_value=[_row(stock_id=99)])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache(payload=[_row(stock_id=7)])

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert [r["stock_id"] for r in rows] == [7]
    fetch.assert_not_awaited()
    assert cache.sets == []  # hits do not rewrite the entry


async def test_cache_miss_writes_payload_with_key_and_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    fetch = AsyncMock(return_value=[_row(stock_id=1), _row(stock_id=2, symbol="600004")])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache()

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    fetch.assert_awaited_once()
    assert rows == [_row(stock_id=1), _row(stock_id=2, symbol="600004")]
    assert mss.SNAPSHOT_TTL == 300
    assert cache.sets == [
        (DAY_KEY, [_row(stock_id=1), _row(stock_id=2, symbol="600004")], mss.SNAPSHOT_TTL)
    ]


async def test_load_day_rows_without_cache_still_fetches(monkeypatch: pytest.MonkeyPatch) -> None:
    """``cache=None`` is legal (duck-typed cache is optional) and must not be probed."""
    fetch = AsyncMock(return_value=[_row()])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=None)

    assert rows == [_row()]
    fetch.assert_awaited_once()


async def test_empty_day_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty day must not freeze a 300s "no data" entry (documented choice)."""
    fetch = AsyncMock(return_value=[])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache()

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert rows == []
    assert cache.sets == []


@pytest.mark.parametrize(
    "bad_payload",
    [
        {"not": "a list"},
        "market:day:rows",
        [{"stock_id": 1}],  # missing keys
        [{**_row(), "stock_id": "not-a-number"}],  # uncoercible id
        [{**_row(), "close": "9.1"}],  # numeric field shipped as a string
        [_row(), ["not", "a", "dict"]],
    ],
)
async def test_malformed_cache_payload_falls_back_to_db(
    monkeypatch: pytest.MonkeyPatch, bad_payload: Any
) -> None:
    """Any unusable payload is a miss — never an exception, never half-stale data."""
    fetch = AsyncMock(return_value=[_row(stock_id=42)])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache(payload=bad_payload)

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert rows == [_row(stock_id=42)]
    fetch.assert_awaited_once()


async def test_empty_list_payload_is_a_valid_hit(monkeypatch: pytest.MonkeyPatch) -> None:
    """``[]`` is a well-formed payload: "malformed" is about shape, not emptiness."""
    fetch = AsyncMock(return_value=[_row()])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache(payload=[])

    assert await mss.load_day_rows(AsyncMock(), D16, cache=cache) == []
    fetch.assert_not_awaited()
