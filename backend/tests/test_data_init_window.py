"""单股覆盖任务的窗口上界必须是"上一个交易日"，否则会写入当日未收盘的脏行。

两段：
1. ``data_init.last_completed_trading_day`` —— 窗口上界的纯函数语义；
2. ``scripts.repair_market_day.incomplete_latest_day_rows`` —— 一次性修复 CLI 的
   *选择* 逻辑（哪个日子算脏、会删几行）。DB 只在 repo/count 三个 seam 上被打桩，
   不连网、不连库。
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta

import pytest

from app.services.data_init import last_completed_trading_day
from scripts import repair_market_day as repair


def test_window_upper_bound_is_previous_weekday() -> None:
    assert last_completed_trading_day(date(2026, 9, 17)) == date(2026, 9, 16)  # 周四 → 周三
    assert last_completed_trading_day(date(2026, 9, 14)) == date(2026, 9, 11)  # 周一 → 上周五
    assert last_completed_trading_day(date(2026, 9, 13)) == date(2026, 9, 11)  # 周日 → 上周五


def test_window_upper_bound_never_returns_today() -> None:
    """今天自己永远不是"已收盘"的交易日（含周一/周末的退让口径）。"""
    for offset in range(0, 14):
        today = date(2026, 9, 7) + timedelta(days=offset)
        assert last_completed_trading_day(today) < today
        assert last_completed_trading_day(today).weekday() < 5


# ---------------------------------------------------------------------------
# repair_market_day: 脏行选择逻辑（repo/count 打桩当 seam）
# ---------------------------------------------------------------------------

TODAY = date(2026, 9, 17)  # 周四；上一交易日 = 09-16
YESTERDAY = date(2026, 9, 16)
UNIVERSE = 5513  # 实测 stocks 行数；0.9×5513 = 4961.7


class _NullDb:
    """Stand-in session — 三个 seam 都被打桩，DB 不会被真正访问。"""


def _install_fakes(
    monkeypatch: pytest.MonkeyPatch,
    *,
    candidate: date | None,
    universe: int = UNIVERSE,
    rows: int = 1,
) -> None:
    async def latest_quote_date(db: object) -> date | None:
        return candidate

    async def universe_count(db: object) -> int:
        return universe

    async def rows_on(db: object, day: date) -> int:
        return rows

    monkeypatch.setattr(repair.limit_up_repo, "latest_quote_date", latest_quote_date)
    monkeypatch.setattr(repair, "_universe_count", universe_count)
    monkeypatch.setattr(repair, "_rows_on", rows_on)


async def test_incomplete_latest_day_selected_for_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """最新日 = 今天且只有 1 行（真实脏行形态）→ 选中 09-17。"""
    _install_fakes(monkeypatch, candidate=TODAY, rows=1)

    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (TODAY, 1)


async def test_completed_latest_day_is_never_purged(monkeypatch: pytest.MonkeyPatch) -> None:
    """最新日 <= 上一交易日 → 不是"未收盘脏行"，缺列问题交给 --backfill。"""
    _install_fakes(monkeypatch, candidate=YESTERDAY, rows=1)

    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (None, 0)


async def test_full_latest_day_is_not_purged(monkeypatch: pytest.MonkeyPatch) -> None:
    """今天但行数已满（>= 0.9×universe）→ 不删；边界 4962 通过、4961 才算脏。"""
    _install_fakes(monkeypatch, candidate=TODAY, rows=4962)
    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (None, 0)

    _install_fakes(monkeypatch, candidate=TODAY, rows=4961)
    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (TODAY, 4961)


async def test_empty_table_or_universe_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 daily_quotes / 空 stocks 表都不能触发删除。"""
    _install_fakes(monkeypatch, candidate=None)
    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (None, 0)

    _install_fakes(monkeypatch, candidate=TODAY, universe=0)
    assert await repair.incomplete_latest_day_rows(_NullDb(), today=TODAY) == (None, 0)


async def test_next_day_run_refuses_to_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    """第二天再跑（today=09-18）时 09-17 已是"上一交易日"，不再被当成脏行删除。"""
    _install_fakes(monkeypatch, candidate=TODAY, rows=1)

    assert await repair.incomplete_latest_day_rows(_NullDb(), today=date(2026, 9, 18)) == (None, 0)


async def test_purge_returns_zero_when_nothing_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """无脏行时不发 DELETE（返回 0，无副作用）。"""
    _install_fakes(monkeypatch, candidate=YESTERDAY)

    assert await repair.purge_incomplete_latest_day(_NullDb(), today=TODAY) == (None, 0)


# ---------------------------------------------------------------------------
# 窗口调用点：不是纯函数，而是"任务真的把 helper 用上了"
#
# 上面两个测试只证明 helper 语义正确；若某个覆盖任务把窗口上界改回
# ``date.today()``，它们照旧全绿。下面用冻结的 today + 假 service 锁住每个
# 调用点实际递出去的边界值。
# ---------------------------------------------------------------------------

FROZEN_TODAY = date(2026, 9, 17)  # 周四 → 上一交易日 = 09-16
FROZEN_ASOF = date(2026, 9, 16)


class _FrozenDate(date):
    """冻结 ``date.today()``（只替换 data_init 模块里的 date 名字）。"""

    @classmethod
    def today(cls) -> date:
        return FROZEN_TODAY


class _FakeSession:
    """最小 async-session 替身 —— 只提供 ``async with`` 与 ``commit``。"""

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def commit(self) -> None:
        return None


def _fake_session_factory() -> _FakeSession:
    return _FakeSession()


class _FakeQuoteService:
    """记录 ``list_stocks_missing_daily_coverage(asof_date=...)`` 与逐股 fetch 的 end_date。"""

    def __init__(self) -> None:
        self.coverage_asof: date | None = None
        self.coverage_years: int | None = None
        self.fetch_end_dates: list[date] = []

    async def list_stocks_missing_daily_coverage(
        self, db: object, *, years: int, asof_date: date
    ) -> list[dict]:
        self.coverage_asof = asof_date
        self.coverage_years = years
        return [
            {
                "stock_id": 1,
                "exchange": "Shanghai_Stocks",
                "symbol": "600000",
                "expected_start": asof_date - timedelta(days=365 * years),
                "asof_date": asof_date,  # 与真实 service 一致：回显传入的上界
                "reason": "no_data",
            }
        ]

    async def ingest_daily_quotes_for_stock(
        self,
        db: object,
        *,
        stock_id: int,
        exchange: str,
        symbol: str,
        start_date: date,
        end_date: date,
        save_raw: bool,
    ) -> dict:
        self.fetch_end_dates.append(end_date)
        return {"upserted": 0}


class _EmptyFrame:
    empty = True


class _FakeCalClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def fetch_trade_cal(
        self, *, exchange: str, start_date: str, end_date: str, is_open: str
    ) -> _EmptyFrame:
        self.calls.append((start_date, end_date))
        return _EmptyFrame()


class _FakeBasicService:
    def __init__(self) -> None:
        self.client = _FakeCalClient()


async def test_three_year_quotes_window_uses_last_completed_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """覆盖任务的 asof 与逐股 fetch 的 end_date 都必须是 09-16，不是 09-17。"""
    from app.services import data_init

    monkeypatch.setattr(data_init, "date", _FrozenDate)
    monkeypatch.setattr(data_init, "async_session_factory", _fake_session_factory)
    service = _FakeQuoteService()

    await data_init._ensure_trailing_three_year_daily_quotes(service)  # type: ignore[arg-type]

    assert service.coverage_years == 3
    assert service.coverage_asof == FROZEN_ASOF
    assert service.fetch_end_dates == [FROZEN_ASOF]


async def test_one_year_daily_basic_window_uses_last_completed_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """daily_basic 的 trade_cal 窗口上界必须是 09-16（下界 = 上界 - 370 天）。"""
    from app.services import data_init

    monkeypatch.setattr(data_init, "date", _FrozenDate)
    service = _FakeBasicService()

    await data_init._ensure_trailing_one_year_daily_basic(service)  # type: ignore[arg-type]

    start = (FROZEN_ASOF - timedelta(days=370)).strftime("%Y%m%d")
    assert service.client.calls == [(start, FROZEN_ASOF.strftime("%Y%m%d"))]


class _FakeIndexService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def ingest_index_daily(
        self, db: object, *, ts_code: str, start_date: str, end_date: str
    ) -> dict:
        self.calls.append((ts_code, start_date, end_date))
        return {"upserted": 0}


async def test_index_daily_window_uses_last_completed_trading_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Step 3 指数窗口同样只到 09-16：两个边界同源，盘中不会写未收盘当日。"""
    from app.services import data_init, market_service

    monkeypatch.setattr(data_init, "date", _FrozenDate)
    monkeypatch.setattr(data_init, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(market_service, "_TARGET_INDICES", [{"ts_code": "000001.SH"}])
    service = _FakeIndexService()

    await data_init._ensure_trailing_one_year_index_daily(service)  # type: ignore[arg-type]

    start = (FROZEN_ASOF - timedelta(days=365)).strftime("%Y%m%d")
    assert service.calls == [("000001.SH", start, FROZEN_ASOF.strftime("%Y%m%d"))]


# ---------------------------------------------------------------------------
# repair_market_day: 写入面防线（--backfill 未收盘守卫 / --yes / 派生缓存失效）
# ---------------------------------------------------------------------------


class _FakeIngestService:
    """记录 ``ingest_daily_quotes`` 调用的替身；实例化即入 instances。"""

    instances: list[_FakeIngestService] = []

    def __init__(self, client: object) -> None:
        self.calls: list[str] = []
        _FakeIngestService.instances.append(self)

    async def ingest_daily_quotes(self, db: object, trade_date: str) -> dict:
        self.calls.append(trade_date)
        return {"upserted": 0}


def _install_backfill_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """打桩回填的惰性依赖（网络与 DB 全部替换为记录型替身）。"""
    from app.core.providers import tushare_client
    from app.services import tushare_ingest

    _FakeIngestService.instances = []
    monkeypatch.setattr(tushare_client, "get_tushare_client", lambda: object())
    monkeypatch.setattr(tushare_ingest, "TuShareIngestService", _FakeIngestService)
    monkeypatch.setattr(repair, "async_session_factory", _fake_session_factory)

    async def rows_on(db: object, day: date) -> int:
        return 0

    async def pct_rows_on(db: object, day: date) -> int:
        return 0

    monkeypatch.setattr(repair, "_rows_on", rows_on)
    monkeypatch.setattr(repair, "_pct_chg_rows_on", pct_rows_on)


async def test_backfill_refuses_unfinished_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """--backfill <today>（盘中）必须空跑：不得重拉半个市场再造脏行。"""
    _install_backfill_fakes(monkeypatch)

    coverage = await repair.backfill_days([TODAY], today=TODAY)

    assert coverage == {}
    assert all(svc.calls == [] for svc in _FakeIngestService.instances)


async def test_backfill_still_processes_completed_day(monkeypatch: pytest.MonkeyPatch) -> None:
    """守卫不能过宽：昨天（上一已收盘工作日）照旧回填。"""
    _install_backfill_fakes(monkeypatch)

    coverage = await repair.backfill_days([YESTERDAY, TODAY], today=TODAY)

    assert coverage == {YESTERDAY: (0, 0)}
    assert [c for svc in _FakeIngestService.instances for c in svc.calls] == ["20260916"]


def _args(**overrides: object) -> argparse.Namespace:
    base = {
        "purge_incomplete_today": False,
        "yes": False,
        "backfill": None,
        "as_of": TODAY,
    }
    base.update(overrides)
    return argparse.Namespace(**base)


async def test_main_purge_without_yes_never_deletes(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺 --yes 时是 dry run：repair.purge_incomplete_latest_day 不被 await。"""
    _install_fakes(monkeypatch, candidate=TODAY, rows=1)
    monkeypatch.setattr(repair, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(repair, "parse_args", lambda: _args(purge_incomplete_today=True))
    purged: list[tuple[object, date | None]] = []

    async def spy_purge(db: object, *, today: date | None = None) -> tuple[date, int]:
        purged.append((db, today))
        return TODAY, 1

    monkeypatch.setattr(repair, "purge_incomplete_latest_day", spy_purge)

    await repair.main()

    assert purged == []


async def test_main_purge_with_yes_awaits_purge(monkeypatch: pytest.MonkeyPatch) -> None:
    """反向对照：给了 --yes 就必须走到删除（证明上一个测试不是"路径根本没通"）。"""
    _install_fakes(monkeypatch, candidate=TODAY, rows=1)
    monkeypatch.setattr(repair, "async_session_factory", _fake_session_factory)
    monkeypatch.setattr(repair, "parse_args", lambda: _args(purge_incomplete_today=True, yes=True))
    purged: list[date | None] = []

    async def spy_purge(db: object, *, today: date | None = None) -> tuple[date, int]:
        purged.append(today)
        return TODAY, 1

    monkeypatch.setattr(repair, "purge_incomplete_latest_day", spy_purge)
    invalidated: list[bool] = []

    async def spy_invalidate() -> int:
        invalidated.append(True)
        return 1

    monkeypatch.setattr(repair, "_invalidate_market_caches", spy_invalidate)

    await repair.main()

    assert purged == [TODAY]
    assert invalidated == [True]


class _FakeRedis:
    def __init__(self, keys: list[str]) -> None:
        self._keys = keys
        self.deleted: list[str] = []

    async def keys(self, pattern: str) -> list[str]:
        return [k for k in self._keys if k.startswith(pattern.rstrip("*"))]

    async def delete(self, *keys: str) -> int:
        self.deleted.extend(keys)
        return len(keys)


async def test_market_cache_invalidation_drops_market_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """修复成功后派生读缓存（rankings/day/SW 快照）必须失效，且不碰其他键。"""
    from app.core import redis as redis_module

    fake = _FakeRedis(
        ["market:day:latest_complete", "market:rankings:gainers:20", "quote:kline:SH:600000:qfq"]
    )

    async def fake_pool() -> _FakeRedis:
        return fake

    monkeypatch.setattr(redis_module, "get_redis_pool", fake_pool)

    dropped = await repair._invalidate_market_caches()

    assert dropped == 2
    assert fake.deleted == ["market:day:latest_complete", "market:rankings:gainers:20"]


async def test_market_cache_invalidation_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis 挂掉不能让已经落库的修复失败（best-effort）。"""
    from app.core import redis as redis_module

    async def boom() -> None:
        raise ConnectionError("redis down")

    monkeypatch.setattr(redis_module, "get_redis_pool", boom)

    assert await repair._invalidate_market_caches() == 0


async def test_main_backfill_invalidates_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    """main 的回填路径在成功回填后同样触发缓存失效。"""
    monkeypatch.setattr(repair, "parse_args", lambda: _args(backfill=[YESTERDAY]))
    monkeypatch.setattr(repair, "async_session_factory", _fake_session_factory)

    async def fake_backfill(days: list[date], *, today: date | None = None) -> dict:
        return {YESTERDAY: (5487, 5487)}

    monkeypatch.setattr(repair, "backfill_days", fake_backfill)
    invalidated: list[bool] = []

    async def spy_invalidate() -> int:
        invalidated.append(True)
        return 1

    monkeypatch.setattr(repair, "_invalidate_market_caches", spy_invalidate)

    await repair.main()

    assert invalidated == [True]
