"""Task 7 — the day-aggregated market endpoints compute from the shared day snapshot.

What this locks
---------------
1. ``get_distribution`` / ``get_sectors`` / ``get_capital_flow`` / ``get_hot_boards``
   compute their buckets/groups in Python from **one** ``load_day_rows`` call: the
   patched loader is the only row source and the DB session is never executed
   (``_BoomSession`` turns any ``db.execute`` into a hard failure). Task 14 moved
   ``get_hot_boards`` onto the East Money board list first, so the hot-board cases
   here patch that fetch to **fail** (``_patch_eastmoney_failure``) and thereby pin
   the *fallback* path — the East Money path has its own coverage in
   ``test_board_endpoints.py``.
2. The distribution bucket edges, the group keys/counts and the envelope fields.
3. The empty-day path still returns an empty envelope and never loads rows.
4. Shenwan L1 performance deliberately stays on its own rollup statement (it needs
   the ``sw_industry_members`` membership walk, which is not a per-stock day fact)
   and reads the stored ``daily_quotes.pct_chg`` — no per-stock previous-close LATERAL.

Offline: both seams (``load_day_rows`` / ``_sw_performance_rows``) are patched, so
nothing here touches Postgres, Redis or the network.
"""

import inspect
import re
from datetime import date
from typing import Any

import pytest

from app.services import market_day_service as mds
from app.services import market_service
from app.services import market_snapshot_service as mss

D16 = date(2026, 9, 16)


# ---------------------------------------------------------------------------
# Fixtures / seams
# ---------------------------------------------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    """One snapshot row as ``load_day_rows`` returns it (floats, JSON-safe).

    Exactly :data:`market_snapshot_service.ROW_KEYS` — pinned by
    ``test_shared_row_fixture_matches_the_live_row_contract``. Row *order* is the
    caller's list order (the loader's ``ORDER BY stock_id``), so tests that care
    about ordering express it by position/column value, never by a ``stock_id``
    key the contract no longer carries.
    """
    row: dict[str, Any] = {
        "csrc_desc": "银行",
        "province": "上海",
        "pct_chg": 0.0,
        "amount": 100.0,
        "basic_date": "2026-09-16",
    }
    row.update(overrides)
    return row


def test_shared_row_fixture_matches_the_live_row_contract() -> None:
    """The shared row fixture must advertise exactly the loader's live key set.

    It previously still built the pre-Task-8 11-key row (``stock_id`` / ``symbol`` /
    ``name`` / ``close`` / ``circ_mv`` / ``turnover_rate`` / ``total_mv``), so it
    claimed contract fidelity it no longer had while every endpoint test kept
    passing on columns no consumer reads. Fix round 1 trimmed it; this pins both
    sides together so a future column change cannot silently drift the fixture.
    """
    assert set(_row()) == set(mss.ROW_KEYS)


def _resolved(quality: str = "complete", reason: str | None = None) -> mds.MarketDay:
    return mds.MarketDay(D16, quality, reason, 5485, 5513, 1.0, True)  # type: ignore[arg-type]


def _patch_day(monkeypatch: pytest.MonkeyPatch, md: mds.MarketDay | None) -> list[tuple[Any, Any]]:
    """Patch the day resolver; return the recorded ``(db, cache)`` call args.

    The resolver is the *other* Redis consumer on this page (``market:day:latest_complete``),
    so recording its ``cache`` kwarg is what pins the fix for the 4x-uncached-probe bug:
    every endpoint must forward its own cache object here, not call the resolver bare.
    """
    calls: list[tuple[Any, Any]] = []

    async def _resolve(db: Any, *, cache: Any = None) -> mds.MarketDay | None:
        calls.append((db, cache))
        return md

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)
    return calls


def _patch_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict[str, Any]]) -> list[Any]:
    """Patch only the shared loader; return the recorded call args."""
    calls: list[Any] = []

    async def _load(db: Any, day: date, *, cache: Any = None) -> list[dict[str, Any]]:
        calls.append((db, day, cache))
        return rows

    monkeypatch.setattr(market_service, "load_day_rows", _load)
    return calls


def _patch_eastmoney_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the East Money board fetch blow up, so ``get_hot_boards`` takes the local
    fallback. The zero-arg factory raising (not just the fetch) is deliberate: the
    whole ``try`` block must count as "East Money unavailable"."""

    def _boom() -> Any:
        raise RuntimeError("eastmoney unavailable (patched)")

    monkeypatch.setattr(market_service, "get_eastmoney_client", _boom)


class _BoomSession:
    """A session whose every SQL call is a test failure."""

    async def execute(self, *_a: Any, **_kw: Any) -> Any:
        raise AssertionError("endpoint must not run its own SQL (the shared loader replaced it)")

    async def __aenter__(self) -> "_BoomSession":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False


class _RecordingCache:
    """No hits, but every read/write is recorded (no redis, no mock library)."""

    def __init__(self) -> None:
        self.reads: list[str] = []
        self.writes: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.reads.append(key)
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.writes.append((key, value, ttl))


# ---------------------------------------------------------------------------
# get_distribution — bucket edges
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pct_chg", "expected"),
    [
        (-11.0, "跌停"),  # below the limit-down edge there is nothing else
        (-9.51, "跌停"),
        (-9.5, "跌停"),  # bound is inclusive
        (-9.49, ">-7%"),  # ... the legacy label for everything from -9.5 up to -7
        (-7.0, "-5~-7%"),  # the CASE tests `< -7` first, so -7.0 falls through
        (-6.99, "-5~-7%"),
        (-5.0, "-3~-5%"),
        (-4.99, "-3~-5%"),
        (-3.0, "-1~-3%"),
        (-2.99, "-1~-3%"),
        (-1.0, "0~-1%"),
        (-0.99, "0~-1%"),
        (-0.01, "0~-1%"),
        (0.0, "0~1%"),
        (0.99, "0~1%"),
        (1.0, "1~3%"),
        (2.99, "1~3%"),
        (3.0, "3~5%"),
        (4.99, "3~5%"),
        (5.0, ">5%"),  # the retired CASE's trailing ELSE
        (9.49, ">5%"),
        (9.5, "涨停"),  # inclusive
        (11.0, "涨停"),
        (None, "0~1%"),  # missing pct_chg == flat, the same bucket the old ELSE 0 gave
    ],
)
def test_distribution_bucket_boundaries(pct_chg: float | None, expected: str) -> None:
    """Every edge is asserted from both sides, so an off-by-one bound fails."""
    assert market_service._distribution_bucket(pct_chg) == expected


async def test_distribution_end_to_end_counts_and_zero_fill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_day(monkeypatch, _resolved())
    _patch_rows(
        monkeypatch,
        [
            _row(pct_chg=11.0),
            _row(pct_chg=-9.6),
            _row(pct_chg=0.0),
            _row(pct_chg=None),
            _row(pct_chg=2.5),
        ],
    )

    out = await market_service.get_distribution(None)

    items = {item["range"]: item["count"] for item in out["items"]}
    assert [item["range"] for item in out["items"]] == list(market_service._DISTRIBUTION_RANGES)
    assert items["涨停"] == 1
    assert items["跌停"] == 1
    assert items["0~1%"] == 2  # the 0.0 row and the missing-pct_chg row
    assert items["1~3%"] == 1
    assert sum(items.values()) == 5  # every row lands in exactly one bucket
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "complete"


# ---------------------------------------------------------------------------
# get_sectors — CSRC grouping
# ---------------------------------------------------------------------------


async def test_sectors_groups_counts_and_amount_conversion(monkeypatch: pytest.MonkeyPatch) -> None:
    """Groups by ``csrc_desc``: count, mean of the stored pct_chg, amount × 1000."""
    _patch_day(monkeypatch, _resolved())
    _patch_rows(
        monkeypatch,
        [
            _row(csrc_desc="电子", pct_chg=2.0, amount=10.0),
            _row(csrc_desc="电子", pct_chg=4.0, amount=None),
            _row(csrc_desc="银行", pct_chg=-1.0, amount=5.0),
            _row(csrc_desc=None, pct_chg=99.0, amount=1.0),  # no bucket
            _row(csrc_desc="", pct_chg=99.0, amount=1.0),  # no bucket
        ],
    )

    out = await market_service.get_sectors(None)

    assert [item["name"] for item in out["items"]] == ["电子", "银行"]
    assert out["items"][0] == {
        "name": "电子",
        "changePercent": 3.0,  # (2.0 + 4.0) / 2
        "totalMarketCap": 10000.0,  # (10 + 0) 千元 × 1000
        "stockCount": 2,
        "topStocks": [],
    }
    assert out["items"][1]["changePercent"] == -1.0
    assert out["items"][1]["stockCount"] == 1
    assert out["as_of_quality"] == "complete"


async def test_sectors_sorts_desc_and_keeps_top_30(monkeypatch: pytest.MonkeyPatch) -> None:
    """31 industries → the 30 best by average change; the weakest is dropped."""
    _patch_day(monkeypatch, _resolved())
    rows = [_row(csrc_desc=f"行业{i:02d}", pct_chg=float(i)) for i in range(31)]
    _patch_rows(monkeypatch, rows)

    out = await market_service.get_sectors(None)

    assert len(out["items"]) == 30
    assert out["items"][0]["name"] == "行业30"
    assert out["items"][-1]["name"] == "行业01"
    percentages = [item["changePercent"] for item in out["items"]]
    assert percentages == sorted(percentages, reverse=True)


# ---------------------------------------------------------------------------
# get_capital_flow — inflow / outflow split
# ---------------------------------------------------------------------------


async def test_capital_flow_split_units_and_missing_pct_chg_is_inflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``pct_chg >= 0`` and a missing ``pct_chg`` both count as inflow (legacy COALESCE)."""
    _patch_day(monkeypatch, _resolved())
    _patch_rows(
        monkeypatch,
        [
            # 电子: 2 inflow rows (one flat, one missing) + 1 outflow → total 300000
            _row(csrc_desc="电子", pct_chg=1.5, amount=100000.0),
            _row(csrc_desc="电子", pct_chg=None, amount=100000.0),
            _row(csrc_desc="电子", pct_chg=-2.0, amount=100000.0),
            # 银行: smaller total → ranked second
            _row(csrc_desc="银行", pct_chg=-0.5, amount=50000.0),
        ],
    )

    out = await market_service.get_capital_flow(None)

    assert [item["name"] for item in out["items"]] == ["电子", "银行"]
    assert out["items"][0] == {
        "name": "电子",
        "inflow": 2.0,  # 200000 千元 / 1e5
        "outflow": -1.0,  # 100000 千元 / 1e5, negated
    }
    assert out["items"][1] == {"name": "银行", "inflow": 0.0, "outflow": -0.5}


async def test_capital_flow_ranks_by_total_turnover_and_keeps_top_10(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ordering is by the group's total amount, not by net flow."""
    _patch_day(monkeypatch, _resolved())
    rows = [_row(csrc_desc=f"行业{i:02d}", pct_chg=-1.0, amount=float(i * 1000)) for i in range(12)]
    _patch_rows(monkeypatch, rows)

    out = await market_service.get_capital_flow(None)

    assert len(out["items"]) == 10
    assert out["items"][0]["name"] == "行业11"  # largest turnover, all of it outflow
    assert out["items"][0]["inflow"] == 0.0
    assert out["items"][-1]["name"] == "行业02"


# ---------------------------------------------------------------------------
# get_hot_boards — breadth per board
# ---------------------------------------------------------------------------


async def test_hot_boards_fallback_industry_breadth_partitions_every_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """East Money down => local grouping; counts come from the shared summarizer
    (up + flat + down == the group size)."""
    _patch_day(monkeypatch, _resolved())
    _patch_eastmoney_failure(monkeypatch)
    _patch_rows(
        monkeypatch,
        [
            _row(csrc_desc="电子", pct_chg=3.0),
            _row(csrc_desc="电子", pct_chg=0.0),
            _row(csrc_desc="电子", pct_chg=None),
            _row(csrc_desc="电子", pct_chg=-1.0),
            _row(csrc_desc="银行", pct_chg=-2.0),
        ],
    )

    out = await market_service.get_hot_boards("industry", None)

    assert [item["name"] for item in out["items"]] == ["电子", "银行"]
    assert out["items"][0] == {
        "id": "industry-电子",
        "name": "电子",
        "code": "",
        "changePercent": 0.67,  # (3.0 + 0.0 + -1.0) / 3 — the None stays out of the mean
        "upCount": 1,
        "flatCount": 2,  # 0.0 and None
        "downCount": 1,
        "leaders": [],
    }
    # one member, one down day: the partition sums to the group size, not to fewer
    assert (out["items"][1]["upCount"], out["items"][1]["flatCount"]) == (0, 0)
    assert out["items"][1]["downCount"] == 1
    assert out["as_of"] == "2026-09-16"
    # the degraded payload says so out loud (no BK code, no leaders either)
    assert (out["source"], out["degraded_reason"]) == ("local_grouping", "eastmoney_unavailable")


async def test_hot_boards_fallback_region_groups_by_province_and_keeps_top_10(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``region`` is on the East Money board list too (``t:1``); local ``province``
    grouping is the fallback."""
    _patch_day(monkeypatch, _resolved())
    _patch_eastmoney_failure(monkeypatch)
    rows = [_row(csrc_desc="电子", province=f"省{i:02d}", pct_chg=float(i)) for i in range(12)]
    _patch_rows(monkeypatch, rows)

    out = await market_service.get_hot_boards("region", None)

    assert len(out["items"]) == 10
    assert out["items"][0]["id"] == "region-省11"
    assert out["items"][0]["changePercent"] == 11.0
    assert out["source"] == "local_grouping"
    percentages = [item["changePercent"] for item in out["items"]]
    assert percentages == sorted(percentages, reverse=True)


# ``test_hot_boards_concept_still_returns_empty_envelope_without_the_loader`` was the
# locked-in form of "concept has no data source". Task 14 gave concept a real source
# (East Money ``m:90+t:3``), so that test is gone; the concept contract — non-empty,
# BK-coded, and still no DB read — now lives in ``test_board_endpoints.py``.


# ---------------------------------------------------------------------------
# The four rewritten endpoints no longer run their own SQL
# ---------------------------------------------------------------------------

_DELETED_SEAMS = (
    "_distribution_rows",
    "_sector_rows",
    "_capital_flow_rows",
    "_hot_board_rows",
)


@pytest.mark.parametrize("seam", _DELETED_SEAMS)
def test_per_endpoint_row_seams_are_gone(seam: str) -> None:
    """The duplicated per-endpoint row seams (and their SQL) were deleted."""
    assert not hasattr(market_service, seam)


#: Statement-construction call sites, not SQL keywords: a bare ``SELECT``/``JOIN``
#: substring in a comment or docstring is not SQL, so scanning for prose reds the
#: suite for non-SQL reasons. Kept at all (rather than dropped in favour of the
#: runtime half) because a statement built in a branch the happy path never reaches
#: is invisible to ``_BoomSession``.
_SQL_CALL_SITES = re.compile(r"\b(?:text|select|insert|update|delete)\s*\(|\.execute\s*\(")


@pytest.mark.parametrize(
    "func",
    [
        market_service.get_distribution,
        market_service.get_sectors,
        market_service.get_capital_flow,
        market_service.get_hot_boards,
    ],
    ids=lambda func: func.__name__,
)
def test_rewritten_endpoint_bodies_hold_no_sql(func: Any) -> None:
    """None of the four rewritten bodies may still build or execute a statement.

    The runtime proof is ``test_rewritten_endpoints_never_execute_sql``; this is the
    static half — a query left behind in a retried-on-error branch that the happy path
    never reaches would show up here. It matches call sites only, deliberately: a
    docstring mentioning SQL keywords must not red the suite.
    """
    match = _SQL_CALL_SITES.search(inspect.getsource(func))
    assert match is None, f"{match.group(0)!r} survived the Task 7 rewrite"


#: Task 8: every result key carries the **resolved day** (``{prefix}:{as_of}``), so a
#: repaired/rolled-over day can never be served from the previous day's payload.
#: Task 14 adds ``reads``: ``get_hot_boards`` probes its East Money key (keyed by
#: **today**, not by the resolved day) before falling back to the day-keyed local
#: payload, so the fallback path legitimately reads two keys and writes one.
_REWRITTEN_ENDPOINTS = [
    (
        "distribution",
        [f"market:distribution:{D16.isoformat()}"],
        f"market:distribution:{D16.isoformat()}",
        market_service.get_distribution,
    ),
    (
        "sectors",
        [f"market:sectors:{D16.isoformat()}"],
        f"market:sectors:{D16.isoformat()}",
        market_service.get_sectors,
    ),
    (
        "capital-flow",
        [f"market:capital-flow:{D16.isoformat()}"],
        f"market:capital-flow:{D16.isoformat()}",
        market_service.get_capital_flow,
    ),
    (
        "hot-boards",
        [
            f"market:hot-boards:em:industry:{D16.isoformat()}",
            f"market:hot-boards:industry:{D16.isoformat()}",
        ],
        f"market:hot-boards:industry:{D16.isoformat()}",
        lambda cache: market_service.get_hot_boards("industry", cache),
    ),
]


@pytest.mark.parametrize(("endpoint", "reads", "write_key", "call"), _REWRITTEN_ENDPOINTS)
async def test_rewritten_endpoints_never_execute_sql(
    monkeypatch: pytest.MonkeyPatch, endpoint: str, reads: list[str], write_key: str, call: Any
) -> None:
    """The only row source is the shared loader — the session must stay untouched.

    ``async_session_factory`` is replaced with a session whose ``execute`` raises, so
    any surviving per-endpoint query fails loudly instead of quietly passing on a
    mocked-out DB. A recording cache exercises the envelope read/write path too.
    """
    day_calls = _patch_day(
        monkeypatch, _resolved(quality="fallback", reason="latest_day_incomplete")
    )
    calls = _patch_rows(monkeypatch, [_row(pct_chg=1.0)])
    monkeypatch.setattr(market_service, "async_session_factory", _BoomSession)
    # hot-boards: East Money unavailable (its key is stamped with "today" = D16)
    _patch_eastmoney_failure(monkeypatch)
    monkeypatch.setattr(market_service, "_today_sh", lambda: D16)
    cache = _RecordingCache()

    out = await call(cache)

    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["as_of_reason"] == "latest_day_incomplete"
    assert out["items"], f"{endpoint} produced no items from the snapshot rows"
    # the loader was called exactly once, with the resolved day and this caller's cache
    assert len(calls) == 1
    assert calls[0][1] == D16
    assert calls[0][2] is cache
    # ... and so was the day resolver: a bare call here re-ran its ~5-statement
    # completeness probe (re-reading market:day:latest_complete) once per endpoint,
    # which is exactly the regression this pins.
    assert len(day_calls) == 1
    assert day_calls[0][1] is cache
    # this module reads/writes only its own result key(s); the snapshot key is the loader's
    assert cache.reads == reads
    assert [key for key, _value, _ttl in cache.writes] == [write_key]


class _SeededCache:
    """Cache with preset payloads keyed by exact key; records reads/writes (no redis)."""

    def __init__(self, seeds: dict[str, dict[str, Any]]) -> None:
        self.seeds = seeds
        self.reads: list[str] = []
        self.writes: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:
        self.reads.append(key)
        return self.seeds.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.writes.append((key, value, ttl))


@pytest.mark.parametrize(
    ("prefix", "extra_reads", "call"),
    [
        ("market:distribution", [], market_service.get_distribution),
        ("market:sectors", [], market_service.get_sectors),
        ("market:capital-flow", [], market_service.get_capital_flow),
        (
            "market:hot-boards:industry",
            [f"market:hot-boards:em:industry:{D16.isoformat()}"],
            lambda cache: market_service.get_hot_boards("industry", cache),
        ),
    ],
)
async def test_result_cache_key_includes_the_resolved_day(
    monkeypatch: pytest.MonkeyPatch, prefix: str, extra_reads: list[str], call: Any
) -> None:
    """A payload cached for another day must not be served for the resolved day.

    Two poisons are seeded: the day-less pre-Task-8 key and the previous day's key. Only
    an implementation whose key carries the resolved ``as_of`` misses both and recomputes;
    any day-agnostic key hits one of them and replays the wrong ``as_of`` label.
    """
    day_key = f"{prefix}:{D16.isoformat()}"
    _patch_day(monkeypatch, _resolved())
    calls = _patch_rows(monkeypatch, [_row(pct_chg=1.0)])
    # hot-boards: the East Money probe (keyed by today = D16) must miss, then the
    # day-keyed local fallback must miss the two seeded poisons and recompute.
    _patch_eastmoney_failure(monkeypatch)
    monkeypatch.setattr(market_service, "_today_sh", lambda: D16)
    cache = _SeededCache(
        {
            prefix: {"as_of": "2026-09-15", "as_of_quality": "complete", "items": []},
            f"{prefix}:2026-09-15": {"as_of": "2026-09-15", "items": []},
        }
    )

    out = await call(cache)

    assert out["as_of"] == D16.isoformat()
    assert out["items"], "a poisoned hit would have produced no items"
    assert len(calls) == 1, "the loader must run for the resolved day"
    assert cache.reads == [*extra_reads, day_key]
    assert [key for key, _v, _t in cache.writes] == [day_key]


async def test_empty_day_returns_empty_envelope_and_never_loads_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No resolved day → ``as_of=None`` / ``partial`` / ``items=[]`` and no loader call.

    ``get_hot_boards`` is the Task-14 exception: its *fallback* envelope carries the
    extra ``source``/``degraded_reason`` discriminator, so it is asserted separately
    (and its East Money fetch is patched out — this module must stay offline).
    """
    _patch_day(monkeypatch, None)
    _patch_eastmoney_failure(monkeypatch)

    async def _boom(*_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        raise AssertionError("no resolved day => the day loader must not run")

    monkeypatch.setattr(market_service, "load_day_rows", _boom)
    cache = _RecordingCache()

    for out in (
        await market_service.get_distribution(cache),  # type: ignore[arg-type]
        await market_service.get_sectors(cache),  # type: ignore[arg-type]
        await market_service.get_capital_flow(cache),  # type: ignore[arg-type]
    ):
        assert out == {
            "as_of": None,
            "as_of_quality": "partial",
            "as_of_reason": None,
            "items": [],
        }
    assert await market_service.get_hot_boards("industry", cache) == {  # type: ignore[arg-type]
        "as_of": None,
        "as_of_quality": "partial",
        "as_of_reason": None,
        "source": "local_grouping",
        "degraded_reason": "eastmoney_unavailable",
        "items": [],
    }
    assert cache.writes == []  # nothing is cached for an unresolved day


# ---------------------------------------------------------------------------
# Shenwan L1 performance — deliberately NOT routed through the day snapshot
# ---------------------------------------------------------------------------


def test_sw_performance_sql_has_no_prev_close_recomputation() -> None:
    """Option (b): SW keeps its own rollup statement, but reads the stored pct_chg.

    The rollup needs the ``sw_industry_members`` → L3 → L2 → L1 membership walk, which
    is not a per-stock day fact: a symbol can belong to several L1s, so there is no
    single value to hang on a snapshot row. This test pins the reduced cost of that
    choice — no ``LEFT JOIN LATERAL`` and no ``trade_date <`` previous-close lookup.
    """
    sql = str(market_service._SW_PERF_SQL)
    assert "avg(q.pct_chg)" in sql
    assert re.search(r"trade_date\s*<", sql) is None
    assert "LATERAL" not in sql
    assert "sw_industry_members" in sql


async def test_sw_performance_does_not_touch_the_shared_loader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Its rows come from its own seam; the envelope fields are unchanged."""
    _patch_day(monkeypatch, _resolved(quality="fallback", reason="latest_day_incomplete"))

    async def _boom(*_a: Any, **_kw: Any) -> list[dict[str, Any]]:
        raise AssertionError("SW rollup must not consume the per-stock day snapshot")

    monkeypatch.setattr(market_service, "load_day_rows", _boom)

    async def _rows(_db: Any, _day_: date) -> list[dict[str, Any]]:
        return [
            {
                "code": "110000",
                "name": "农林牧渔",
                "member_count": 3,
                "avg_pct_chg": 1.25,
                "total_amount": 123.0,
                "up_count": 2,
                "down_count": 1,
            }
        ]

    monkeypatch.setattr(market_service, "_sw_performance_rows", _rows)

    out = await market_service.get_sw_industry_performance(None, None, 31)

    assert out.as_of == D16
    assert out.as_of_quality == "fallback"
    assert out.as_of_reason == "latest_day_incomplete"
    assert [item.name for item in out.items] == ["农林牧渔"]
