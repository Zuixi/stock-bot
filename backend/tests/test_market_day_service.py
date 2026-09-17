"""日级完整性判据：行数 + pct_chg 非空率 + 涨跌停价存在。"""

from datetime import date

import pytest

from app.services import market_day_service as mds


def test_complete_only_when_all_three_conditions_hold() -> None:
    assert mds.is_day_complete(5485, 5513, 1.0, True) is True
    # 行数够但列全 NULL（09-14/15 的真实形态）→ 不完整
    assert mds.is_day_complete(5485, 5513, 0.0002, True) is False
    # 行数够、列满，但缺该日涨跌停价 → 不完整
    assert mds.is_day_complete(5485, 5513, 1.0, False) is False
    # 行数不足（脏行日：1 行）→ 不完整
    assert mds.is_day_complete(1, 5513, 1.0, False) is False
    # 阈值边界：0.9×5513 = 4961.7 → 4962 通过、4961 不通过
    assert mds.is_day_complete(4962, 5513, 0.99, True) is True
    assert mds.is_day_complete(4961, 5513, 0.99, True) is False


def test_universe_zero_is_not_complete() -> None:
    """空 stocks 表时不能把"0/0"判成完整。"""
    assert mds.is_day_complete(0, 0, 1.0, True) is False


# ---------------------------------------------------------------------------
# resolve_latest_complete_day — orchestration + cache contract
#
# The repository helpers are the DB seam, so they are faked here: these tests
# assert the decision logic (walk-back window, quality/reason) and the JSON-safe
# cache contract without touching a database.
# ---------------------------------------------------------------------------

D = date
CANDIDATE = D(2026, 9, 17)
CLEAN_DAY = D(2026, 9, 16)
WINDOW = [D(2026, 9, 11), D(2026, 9, 14), D(2026, 9, 15), D(2026, 9, 16), D(2026, 9, 17)]

# (rows, pct_chg_ratio, limits_present)
_ROWS_ONLY = (5485, 0.0002, True)  # 行数够但 pct_chg 全 NULL（09-14/15 真实形态）
_DIRTY_DAY = (1, 1.0, False)  # 当日脏行（未收盘的 1 行）
_CLEAN = (5485, 1.0, True)
ALL_INCOMPLETE = {
    D(2026, 9, 11): _ROWS_ONLY,
    D(2026, 9, 14): _ROWS_ONLY,
    D(2026, 9, 15): _ROWS_ONLY,
    D(2026, 9, 16): _ROWS_ONLY,
    D(2026, 9, 17): _DIRTY_DAY,
}
ONLY_16_COMPLETE = {**ALL_INCOMPLETE, D(2026, 9, 16): _CLEAN}
CANDIDATE_COMPLETE = {**ONLY_16_COMPLETE, D(2026, 9, 17): _CLEAN}


class _NullDb:
    """Stand-in session — every repository helper is faked in these tests."""


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


def _install_repo_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stats: dict[date, tuple[int, float, bool]],
    dates: list[date] = WINDOW,
    universe: int = 5513,
    candidate: date | None = CANDIDATE,
) -> dict[str, list[object]]:
    calls: dict[str, list[object]] = {"day_stats": [], "limit": []}

    async def latest_quote_date(db: object) -> date | None:
        return candidate

    async def list_recent_trade_dates(db: object, as_of: date, limit: int) -> list[date]:
        calls["limit"].append(limit)
        return sorted(d for d in dates if d <= as_of)

    async def universe_count(db: object) -> int:
        return universe

    async def day_stats(db: object, day: date) -> tuple[int, float, bool]:
        calls["day_stats"].append(day)
        return stats[day]

    monkeypatch.setattr(mds.limit_up_repo, "latest_quote_date", latest_quote_date)
    monkeypatch.setattr(mds.limit_up_repo, "list_recent_trade_dates", list_recent_trade_dates)
    monkeypatch.setattr(mds, "_universe_count", universe_count)
    monkeypatch.setattr(mds, "_day_stats", day_stats)
    return calls


async def test_resolve_empty_daily_quotes_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无任何行情时返回 None（不抛，区别于 legacy get_latest_trade_date）。"""
    _install_repo_fakes(monkeypatch, stats={}, candidate=None)

    assert await mds.resolve_latest_complete_day(_NullDb()) is None  # type: ignore[arg-type]


async def test_resolve_falls_back_to_last_complete_day_in_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _install_repo_fakes(monkeypatch, stats=ONLY_16_COMPLETE)

    out = await mds.resolve_latest_complete_day(_NullDb())  # type: ignore[arg-type]

    assert out == mds.MarketDay(
        day=CLEAN_DAY,
        quality="fallback",
        reason="latest_day_incomplete",
        rows=5485,
        universe=5513,
        pct_chg_ratio=1.0,
        limits_present=True,
    )
    # walk-back window is the constant, and it stops at the first complete day
    assert calls["limit"] == [mds.FALLBACK_LOOKBACK_DAYS]
    assert calls["day_stats"] == [CANDIDATE, CLEAN_DAY]


async def test_resolve_marks_candidate_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _install_repo_fakes(monkeypatch, stats=CANDIDATE_COMPLETE)

    out = await mds.resolve_latest_complete_day(_NullDb())  # type: ignore[arg-type]

    assert out is not None
    assert (out.day, out.quality, out.reason) == (CANDIDATE, "complete", None)
    assert calls["day_stats"] == [CANDIDATE]


async def test_resolve_returns_candidate_partial_when_nothing_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """窗口内全不完整 → 仍返回最新日，但标 partial 让下游能提示数据不新鲜。"""
    calls = _install_repo_fakes(monkeypatch, stats=ALL_INCOMPLETE)

    out = await mds.resolve_latest_complete_day(_NullDb())  # type: ignore[arg-type]

    assert out == mds.MarketDay(
        day=CANDIDATE,
        quality="partial",
        reason="latest_day_incomplete",
        rows=1,
        universe=5513,
        pct_chg_ratio=1.0,
        limits_present=False,
    )
    assert calls["day_stats"] == list(reversed(WINDOW))


async def test_resolve_cache_hit_skips_db(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RecordingCache()
    cache.store[mds.CACHE_KEY] = {
        "day": "2026-09-16",
        "quality": "fallback",
        "reason": "latest_day_incomplete",
        "rows": 5485,
        "universe": 5513,
        "pct_chg_ratio": 1.0,
        "limits_present": True,
    }

    async def boom() -> None:
        raise AssertionError("resolver must not touch the DB on a cache hit")

    monkeypatch.setattr(mds.limit_up_repo, "latest_quote_date", boom)

    out = await mds.resolve_latest_complete_day(_NullDb(), cache=cache)  # type: ignore[arg-type]

    assert out is not None and out.day == CLEAN_DAY and out.quality == "fallback"
    assert cache.set_calls == []


async def test_resolve_caches_json_safe_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = RecordingCache()
    _install_repo_fakes(monkeypatch, stats=ONLY_16_COMPLETE)

    await mds.resolve_latest_complete_day(_NullDb(), cache=cache)  # type: ignore[arg-type]

    assert cache.set_calls == [
        (
            "market:day:latest_complete",
            {
                "day": "2026-09-16",
                "quality": "fallback",
                "reason": "latest_day_incomplete",
                "rows": 5485,
                "universe": 5513,
                "pct_chg_ratio": 1.0,
                "limits_present": True,
            },
            60,
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"day": "not-a-date"},  # unparseable day
        {"day": "2026-09-16"},  # missing keys
        "2026-09-16",  # wrong type entirely
    ],
)
async def test_resolve_malformed_cache_is_a_miss(
    monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    cache = RecordingCache()
    cache.store[mds.CACHE_KEY] = payload
    _install_repo_fakes(monkeypatch, stats=ONLY_16_COMPLETE)

    out = await mds.resolve_latest_complete_day(_NullDb(), cache=cache)  # type: ignore[arg-type]

    assert out is not None and out.day == CLEAN_DAY
    # the bad payload is overwritten with a good one
    assert cache.store[mds.CACHE_KEY] == {
        "day": "2026-09-16",
        "quality": "fallback",
        "reason": "latest_day_incomplete",
        "rows": 5485,
        "universe": 5513,
        "pct_chg_ratio": 1.0,
        "limits_present": True,
    }
