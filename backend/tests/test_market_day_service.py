"""日级完整性判据：行数 + pct_chg 非空率 + 涨跌停价存在。"""

from datetime import date, datetime
from zoneinfo import ZoneInfo

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
    """无任何行情时返回 None（不抛，区别于已删除的 legacy get_latest_trade_date）。"""
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


# ---------------------------------------------------------------------------
# 当日盘后（T+0）期望日 —— 纯函数 + 日历缓存
#
# 这一组钉死的是「期望日只由日历与时钟决定」这条边界：判据**不得**去看数据齐备性
# （那会造成对账鸡生蛋死锁，并让读面新鲜度退回自指）。见
# plans/2026-10-16-t0-after-close-expected-latest.md §三 / §四.4-5。
# ---------------------------------------------------------------------------

SH = ZoneInfo("Asia/Shanghai")

# 2026-10-15 周四 / 10-16 周五 / 10-17 周六（非交易日）/ 10-19 周一
T14, T15, T16, SAT17, T19 = (
    D(2026, 10, 14),
    D(2026, 10, 15),
    D(2026, 10, 16),
    D(2026, 10, 17),
    D(2026, 10, 19),
)
CAL = [T14, T15, T16, T19]  # 日历里刻意不含周六（真实日历也不会含）


def _at(day: date, hour: int, minute: int) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=SH)


def test_expected_day_after_cutoff_includes_today() -> None:
    """交易日 cutoff 之后 ⇒ 当日（这就是"16 号晚上该出 16 号"的那一条）。"""
    assert mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 17, 30)) == T16
    assert mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 18, 0)) == T16
    assert mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 23, 59)) == T16


def test_expected_day_before_cutoff_is_previous_trading_day() -> None:
    """cutoff 之前当日数据还没出全 ⇒ 期望日仍是上一个交易日。"""
    assert mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 17, 29)) == T15
    assert mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 9, 0)) == T15


def test_expected_day_on_non_trading_day_is_previous() -> None:
    """周六（不在日历里）⇒ 上一个交易日；cutoff 与周末无关。"""
    assert (
        mds.calendar_expected_latest_day(CAL, covered_through=SAT17, now=_at(SAT17, 10, 0)) == T16
    )
    assert (
        mds.calendar_expected_latest_day(CAL, covered_through=SAT17, now=_at(SAT17, 23, 0)) == T16
    )


def test_expected_day_requires_calendar_covering_today() -> None:
    """缓存没覆盖今天 ⇒ 判不出（**不许**把昨天当期望日而自称新鲜）。

    这是"休市"与"缓存停在昨天"的分界：两者都表现为"今天不在日历里"，只有
    `covered_through` 能把它们分开。若返回昨天，读面会报 `stale=false`。
    """
    assert mds.calendar_expected_latest_day(CAL, covered_through=T15, now=_at(T16, 20, 0)) is None
    assert mds.calendar_expected_latest_day(CAL, covered_through=None, now=_at(T16, 20, 0)) is None


def test_expected_day_empty_or_future_calendar_is_none() -> None:
    assert mds.calendar_expected_latest_day([], covered_through=T16, now=_at(T16, 20, 0)) is None
    # 日历全在"今天"之后（时钟回拨/脏缓存）→ 无可选交易日，判不出
    assert mds.calendar_expected_latest_day([T19], covered_through=T19, now=_at(T16, 20, 0)) is None


def test_expected_day_first_calendar_day_before_cutoff_is_none() -> None:
    """今天是日历里第一个交易日、且未到 cutoff ⇒ 无更早的交易日可选 ⇒ None（不是今天）。"""
    assert mds.calendar_expected_latest_day([T16], covered_through=T16, now=_at(T16, 10, 0)) is None


def test_expected_day_naive_and_aware_now_agree() -> None:
    """naive 输入按**上海本地时间**解释（容器默认 UTC，按 UTC 解释会让 T+0 永不触发）。"""
    naive = datetime(2026, 10, 16, 18, 0)
    assert mds.calendar_expected_latest_day(
        CAL, covered_through=T16, now=naive
    ) == mds.calendar_expected_latest_day(CAL, covered_through=T16, now=_at(T16, 18, 0))


def test_expected_day_ignores_data_completeness() -> None:
    """判据**只**看日历与时钟：签名里根本没有行数/齐备性参数（防回归成自指）。"""
    import inspect

    params = set(inspect.signature(mds.calendar_expected_latest_day).parameters)
    assert params == {"calendar", "covered_through", "now"}


# --------------------------------------------------------------- 日历缓存契约


def test_calendar_payload_is_json_safe_and_round_trips() -> None:
    """缓存载荷必须全是 str（orjson 不认 date），且往返后类型仍是 date。"""
    payload = mds.calendar_cache_payload([T16, T14, T15, T14], T16)
    assert payload == {
        "days": ["2026-10-14", "2026-10-15", "2026-10-16"],
        "covered_through": "2026-10-16",
    }
    assert all(isinstance(item, str) for item in payload["days"])

    days, covered = mds.parse_calendar_payload(payload)  # type: ignore[misc]
    assert days == [T14, T15, T16]
    assert covered == T16
    assert all(isinstance(item, date) for item in days)


@pytest.mark.parametrize(
    "payload",
    [
        None,
        "2026-10-16",
        [],
        {},
        {"days": "2026-10-16"},
        {"days": ["not-a-date"], "covered_through": "2026-10-16"},
        {"days": ["2026-10-16"], "covered_through": "not-a-date"},
    ],
)
def test_calendar_payload_malformed_is_a_miss(payload: object) -> None:
    """形状不对一律当缓存未命中（不抛）——缓存坏了不能让 GET 变 500。"""
    assert mds.parse_calendar_payload(payload) is None


async def test_write_calendar_cache_contract() -> None:
    cache = RecordingCache()
    await mds.write_calendar_cache(cache, days=[T16, T15], covered_through=T16)
    assert cache.set_calls == [
        (
            mds.CALENDAR_CACHE_KEY,
            {"days": ["2026-10-15", "2026-10-16"], "covered_through": "2026-10-16"},
            mds.CALENDAR_CACHE_TTL,
        )
    ]


async def test_write_calendar_cache_noop_without_cache_or_days() -> None:
    await mds.write_calendar_cache(None, days=[T16], covered_through=T16)  # 不抛即可
    cache = RecordingCache()
    await mds.write_calendar_cache(cache, days=[], covered_through=T16)
    assert cache.set_calls == []


# ----------------------------------------------------------- 读面编排


async def test_resolve_expected_day_hit() -> None:
    cache = RecordingCache()
    await mds.write_calendar_cache(cache, days=CAL, covered_through=T16)

    assert await mds.resolve_expected_day(cache, now=_at(T16, 20, 0)) == (T16, None)
    # 同日 cutoff 之前 → 上一交易日（同一份缓存，不同时钟）
    assert await mds.resolve_expected_day(cache, now=_at(T16, 12, 0)) == (T15, None)


async def test_resolve_expected_day_reasons() -> None:
    """判不出时必须给出原因，而不是退回某个"看起来合理"的日子。"""
    assert await mds.resolve_expected_day(None, now=_at(T16, 20, 0)) == (None, "no_cache")

    empty = RecordingCache()
    assert await mds.resolve_expected_day(empty, now=_at(T16, 20, 0)) == (
        None,
        "calendar_cache_miss",
    )

    stale = RecordingCache()
    await mds.write_calendar_cache(stale, days=CAL, covered_through=T15)
    assert await mds.resolve_expected_day(stale, now=_at(T16, 20, 0)) == (
        None,
        "calendar_not_covering_today",
    )


async def test_resolve_expected_day_disabled_by_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """env 开关关掉 ⇒ 判不出（调用方据此走旧锚），而不是悄悄按新语义给一个日期。"""
    cache = RecordingCache()
    await mds.write_calendar_cache(cache, days=CAL, covered_through=T16)
    monkeypatch.setattr(mds.settings, "t0_after_close_enabled", False)

    assert await mds.resolve_expected_day(cache, now=_at(T16, 20, 0)) == (
        None,
        "t0_after_close_disabled",
    )


async def test_resolve_expected_day_ignores_data_completeness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判据不得读库：编排层连 session 都没有，任何 DB 访问都无从发生（结构性护栏）。

    对应 §四.4 的"今天整天没采也必须报出滞后"：只要时钟过了 cutoff，期望日就是**今天**，
    与 `daily_quotes` 里有没有今天无关。
    """
    cache = RecordingCache()
    await mds.write_calendar_cache(cache, days=CAL, covered_through=T16)
    monkeypatch.setattr(
        mds.limit_up_repo,
        "latest_quote_date",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not touch the DB")),
    )

    assert await mds.resolve_expected_day(cache, now=_at(T16, 20, 0)) == (T16, None)
