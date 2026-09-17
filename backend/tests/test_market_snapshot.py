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


def _db_row(**overrides: Any) -> dict[str, Any]:
    """One row as the DB returns it: the numeric columns are ``Decimal``."""
    row = _row(
        close=Decimal("9.1000"),
        pct_chg=Decimal("-0.8715"),
        amount=Decimal("656348.14"),
        total_mv=Decimal("30308312.85"),
        circ_mv=Decimal("30308312.85"),
        turnover_rate=Decimal("0.2172"),
    )
    row.update(overrides)
    return row


class _FakeResult:
    """Minimal stand-in for SQLAlchemy's ``Result`` (only ``.mappings()`` is used)."""

    def __init__(self, mappings: list[dict[str, Any]]) -> None:
        self._mappings = mappings

    def mappings(self) -> list[dict[str, Any]]:
        return self._mappings


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
    # the counts partition the group (new convention: NULL counts as flat)
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
    """No non-null pct_chg → 0.0 mean, all flat."""
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


async def test_cache_hit_preserves_payload_row_order(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hits return the cached rows verbatim; the writer stored them stock_id-ordered.

    Two-plus out-of-order rows are used so a hidden ``sorted()``/``dict`` regroup
    on the hit path would show up, and ``None`` string fields prove the
    ``str | None`` type check is not over-strict.
    """
    fetch = AsyncMock(return_value=[_row(stock_id=1)])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    payload = [
        _row(stock_id=9, name=None),
        _row(stock_id=3),
        _row(stock_id=5, csrc_desc=None),
    ]
    cache = _FakeCache(payload=payload)

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert rows == payload  # verbatim order
    assert [r["stock_id"] for r in rows] == [9, 3, 5]
    assert rows[0]["name"] is None
    assert rows[2]["csrc_desc"] is None
    fetch.assert_not_awaited()
    assert cache.sets == []


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


async def test_cold_path_normalizes_decimal_db_rows_to_json_safe_floats(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cold path normalizes raw DB ``Decimal`` mappings exactly once.

    ``db.execute`` (not the ``_fetch_rows`` seam) is faked, so this drives the
    real fetch→normalize→cache-write path. ``_FakeCache.set`` runs a strict
    ``json.dumps`` (no ``default=``), so a leaked ``Decimal`` would raise here.
    """
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeResult([_db_row()]))
    cache = _FakeCache()

    rows = await mss.load_day_rows(db, D16, cache=cache)

    assert rows == [_row()]
    assert all(isinstance(rows[0][key], float) for key in mss._FLOAT_KEYS)
    assert cache.sets == [(DAY_KEY, [_row()], mss.SNAPSHOT_TTL)]


async def test_cold_path_normalizes_each_row_exactly_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One miss must touch ``_normalize_row`` once per row (fetch + cache write share it).

    Before the fix ``_fetch_rows`` normalized and ``_to_payload`` normalized again:
    two rows cost four calls (~6.5ms/5485 rows of redundant work) on every miss.
    """
    real = mss._normalize_row
    calls = 0

    def counting(row: Any) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return real(row)

    monkeypatch.setattr(mss, "_normalize_row", counting)
    db = AsyncMock()
    db.execute = AsyncMock(return_value=_FakeResult([_db_row(), _db_row(stock_id=2)]))
    cache = _FakeCache()

    rows = await mss.load_day_rows(db, D16, cache=cache)

    assert [r["stock_id"] for r in rows] == [1, 2]
    assert calls == 2  # not 4


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
        [{**_row(), "csrc_desc": {"a": 1}}],  # string field shipped as a dict
        [{**_row(), "province": 123}],  # string field shipped as an int
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


async def test_overflowing_json_number_payload_falls_back_to_db(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A >308-digit JSON integer makes ``float()`` raise ``OverflowError``.

    That must degrade to a cache miss like every other unusable payload — it must
    not escape into the request path.
    """
    fetch = AsyncMock(return_value=[_row(stock_id=42)])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache(payload=[{**_row(), "close": 10**400}])

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert rows == [_row(stock_id=42)]
    fetch.assert_awaited_once()


async def test_empty_list_payload_is_treated_as_a_miss(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``[]`` is a miss, not a hit.

    This module never writes an empty list (only non-empty days are cached), and a
    stray ``[]`` would freeze a "no data" day for the whole TTL — the exact state
    the non-empty write rule exists to prevent.
    """
    fetch = AsyncMock(return_value=[_row(stock_id=42)])
    monkeypatch.setattr(mss, "_fetch_rows", fetch)
    cache = _FakeCache(payload=[])

    rows = await mss.load_day_rows(AsyncMock(), D16, cache=cache)

    assert rows == [_row(stock_id=42)]
    fetch.assert_awaited_once()
