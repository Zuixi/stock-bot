"""单股覆盖任务的窗口上界必须是"上一个交易日"，否则会写入当日未收盘的脏行。

两段：
1. ``data_init.last_completed_trading_day`` —— 窗口上界的纯函数语义；
2. ``scripts.repair_market_day.incomplete_latest_day_rows`` —— 一次性修复 CLI 的
   *选择* 逻辑（哪个日子算脏、会删几行）。DB 只在 repo/count 三个 seam 上被打桩，
   不连网、不连库。
"""

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
