"""短线热度股追踪：`build_stock_history_rows` 纯函数 + 服务装配 + 路由单测。

门禁口径（`TUSHARE_TOKEN= uv run pytest tests/test_limit_up_stock_history.py`）：
**无真实 DB、无 HTTP**——仓储调用全部 monkeypatch 替身。

守住的四条口径：

1. 状态顺序固定 `is_lu > is_zb > prev_is_lu > none`（连板票 `prev_is_lu` 也为真，
   顺序反了会把涨停画成"昨日涨停"）。
2. `streak` 只在 `limit_up` 格有值；炸板/断板/未上榜为 `None`（缺失 ≠ 0）。
3. 某股某日无事实行 → `none`（"未上榜"），不是 0 板。
4. `dates` 只含实际有事实的交易日；缺的那天进 `missing_dates` + `degraded_reason`。
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from app.api.v1 import market_data as api
from app.schemas.limit_up import HeatStockHistoryOut
from app.services import limit_up_stock_history_service as svc
from app.services.limit_up_stock_history_service import build_stock_history_rows

D1 = date(2026, 11, 4)
D2 = date(2026, 11, 5)
D3 = date(2026, 11, 6)
DATES = [D1, D2, D3]


def _fact(
    symbol: str,
    trade_date: date,
    *,
    is_lu: bool = False,
    is_zb: bool = False,
    prev_is_lu: bool = False,
    streak: int = 0,
    name: str | None = None,
) -> dict[str, Any]:
    return {
        "trade_date": trade_date,
        "symbol": symbol,
        "name": name or f"{symbol}股份",
        "is_lu": is_lu,
        "is_zb": is_zb,
        "prev_is_lu": prev_is_lu,
        "streak": streak,
    }


# ── 纯函数 ────────────────────────────────────────────────────────────────────


def test_cells_align_and_streak_aggregates() -> None:
    """1→2→3 连板：cells 对齐 dates，max/boards/current 一致。"""
    facts = [
        _fact("600001", D1, is_lu=True, streak=1),
        _fact("600001", D2, is_lu=True, streak=2),
        _fact("600001", D3, is_lu=True, streak=3),
        _fact("600002", D1, is_lu=True, streak=1),
    ]
    rows, total, truncated = build_stock_history_rows(facts, DATES, min_streak=1, limit=10)

    assert (total, truncated) == (2, False)
    row = next(r for r in rows if r["symbol"] == "600001")
    assert [c["status"] for c in row["cells"]] == ["limit_up"] * 3
    assert [c["streak"] for c in row["cells"]] == [1, 2, 3]
    assert row["max_streak"] == 3
    assert row["boards"] == 3
    assert row["broken_days"] == 0
    assert row["current_status"] == "limit_up"
    assert row["current_streak"] == 3
    assert row["last_limit_up"] == D3
    assert row["name"] == "600001股份"


def test_broken_cell_has_no_streak_and_counts() -> None:
    """炸板 → `broken`、`streak=None`、进 `broken_days`、打断 `current_streak`。"""
    facts = [
        _fact("600001", D1, is_lu=True, streak=2),
        _fact("600001", D2, is_zb=True),
        _fact("600001", D3, prev_is_lu=True),
    ]
    rows, _, _ = build_stock_history_rows(facts, DATES, min_streak=1, limit=10)
    row = rows[0]

    assert [c["status"] for c in row["cells"]] == ["limit_up", "broken", "dropped"]
    assert row["cells"][1]["streak"] is None
    assert row["broken_days"] == 1
    assert row["current_status"] == "dropped"
    assert row["current_streak"] == 0
    assert row["last_limit_up"] == D1


def test_dropped_when_prev_is_lu_only() -> None:
    """`prev_is_lu=True` 且今日未涨停/未炸板 → `dropped`（断板），不是 `none`。"""
    facts = [_fact("600001", D2, prev_is_lu=True)]
    rows, _, _ = build_stock_history_rows(facts, DATES, min_streak=0, limit=10)
    assert rows[0]["cells"][1] == {"status": "dropped", "streak": None}


def test_missing_day_is_none_not_zero_boards() -> None:
    """某日无事实行 → `none`（未上榜），`streak=None`，不计入 boards。"""
    facts = [_fact("600001", D1, is_lu=True, streak=1), _fact("600001", D3, is_lu=True, streak=1)]
    rows, _, _ = build_stock_history_rows(facts, DATES, min_streak=1, limit=10)
    row = rows[0]
    assert row["cells"][1] == {"status": "none", "streak": None}
    assert row["boards"] == 2
    assert row["max_streak"] == 1
    assert row["last_limit_up"] == D3


def test_min_streak_filter_boundaries() -> None:
    """`2` 只留 max_streak>=2；`1` 留所有涨停过的；`0` 连仅炸板的行也留。"""
    facts = [
        _fact("600001", D1, is_lu=True, streak=1),
        _fact("600001", D2, is_lu=True, streak=2),
        _fact("600002", D1, is_lu=True, streak=1),
        _fact("600003", D2, is_zb=True),
    ]
    rows2, total2, _ = build_stock_history_rows(facts, DATES, min_streak=2, limit=10)
    assert [r["symbol"] for r in rows2] == ["600001"]
    assert total2 == 1

    rows1, total1, _ = build_stock_history_rows(facts, DATES, min_streak=1, limit=10)
    assert sorted(r["symbol"] for r in rows1) == ["600001", "600002"]
    assert total1 == 2

    rows0, total0, _ = build_stock_history_rows(facts, DATES, min_streak=0, limit=10)
    assert total0 == 3
    broken = next(r for r in rows0 if r["symbol"] == "600003")
    assert broken["max_streak"] == 0 and broken["boards"] == 0 and broken["broken_days"] == 1


def test_sort_order_and_limit_truncation() -> None:
    """`current_streak DESC, max_streak DESC, boards DESC, last_limit_up DESC, symbol ASC`。"""
    facts = [
        # 当前 2 板、窗口 2 板
        _fact("600001", D2, is_lu=True, streak=1),
        _fact("600001", D3, is_lu=True, streak=2),
        # 当前 2 板、窗口 3 板（D1/D2 也算）→ 与上面同 current_streak，max_streak 更大
        _fact("600002", D1, is_lu=True, streak=1),
        _fact("600002", D2, is_lu=True, streak=2),
        _fact("600002", D3, is_lu=True, streak=2),
        # 已断：current_streak=0
        _fact("600003", D1, is_lu=True, streak=1),
        _fact("600003", D2, is_zb=True),
        # 完全并列（current/max/boards/last 全同）→ 按 symbol ASC
        _fact("600005", D3, is_lu=True, streak=1),
        _fact("600004", D3, is_lu=True, streak=1),
    ]
    rows, total, truncated = build_stock_history_rows(facts, DATES, min_streak=1, limit=10)
    assert [r["symbol"] for r in rows] == ["600002", "600001", "600004", "600005", "600003"]
    assert (total, truncated) == (5, False)

    limited, total2, truncated2 = build_stock_history_rows(facts, DATES, min_streak=1, limit=3)
    assert [r["symbol"] for r in limited] == ["600002", "600001", "600004"]
    assert (total2, truncated2) == (5, True)


def test_empty_inputs() -> None:
    assert build_stock_history_rows([], DATES, min_streak=0, limit=10) == ([], 0, False)
    only_fact = [_fact("600001", D1, is_lu=True)]
    assert build_stock_history_rows(only_fact, [], min_streak=0, limit=10) == ([], 0, False)


def test_fact_outside_dates_is_ignored() -> None:
    """`dates` 里没有的交易日不建格子（窗口由调用方决定，函数不自行扩张）。"""
    facts = [_fact("600001", date(2026, 1, 2), is_lu=True, streak=1)]
    rows, total, _ = build_stock_history_rows(facts, DATES, min_streak=0, limit=10)
    assert rows == [] and total == 0


# ── 服务装配（monkeypatch 仓储）────────────────────────────────────────────────


class _FakeRepo:
    def __init__(
        self,
        *,
        fact_date: date | None,
        quote_date: date | None,
        trade_dates: list[date],
        facts: list[dict[str, Any]],
    ) -> None:
        self.fact_date = fact_date
        self.quote_date = quote_date
        self.trade_dates = trade_dates
        self.facts = facts
        self.calls: list[tuple[date, date]] = []

    async def latest_limit_up_stock_date(self, db: Any) -> date | None:
        return self.fact_date

    async def latest_quote_date(self, db: Any) -> date | None:
        return self.quote_date

    async def list_recent_trade_dates(self, db: Any, as_of: date, limit: int) -> list[date]:
        return self.trade_dates[-limit:]

    async def list_limit_up_stock_facts_in_range(
        self, db: Any, start: date, end: date
    ) -> list[dict[str, Any]]:
        self.calls.append((start, end))
        return self.facts


@pytest.fixture()
def patch_repo(monkeypatch: pytest.MonkeyPatch):
    def _install(fake: _FakeRepo) -> _FakeRepo:
        for name in (
            "latest_limit_up_stock_date",
            "latest_quote_date",
            "list_recent_trade_dates",
            "list_limit_up_stock_facts_in_range",
        ):
            monkeypatch.setattr(svc.limit_up_repo, name, getattr(fake, name))
        return fake

    return _install


@pytest.mark.asyncio
async def test_service_no_fact_rows_falls_back_to_quote_date(patch_repo) -> None:
    fake = patch_repo(
        _FakeRepo(fact_date=None, quote_date=D3, trade_dates=DATES, facts=[])
    )
    payload = await svc.get_stock_history(object(), days=20, min_streak=2, limit=300)  # type: ignore[arg-type]

    assert payload["as_of"] == D3
    assert payload["degraded_reason"] == "no_fact_rows"
    assert payload["window"] == {"days": 20, "start": None, "end": None, "samples": 0}
    assert payload["dates"] == []
    assert payload["rows"] == [] and payload["total"] == 0 and payload["truncated"] is False
    # 请求窗口的每个交易日都算缺（事实表一行没有）
    assert payload["missing_dates"] == DATES
    assert fake.calls == [(D1, D3)]


@pytest.mark.asyncio
async def test_service_no_any_date(patch_repo) -> None:
    patch_repo(_FakeRepo(fact_date=None, quote_date=None, trade_dates=[], facts=[]))
    payload = await svc.get_stock_history(object())  # type: ignore[arg-type]
    assert payload["as_of"] is None
    assert payload["degraded_reason"] == "no_fact_rows"
    assert payload["missing_dates"] == []


@pytest.mark.asyncio
async def test_service_facts_gap_only_lists_missing_day(patch_repo) -> None:
    fake = patch_repo(
        _FakeRepo(
            fact_date=D3,
            quote_date=D3,
            trade_dates=DATES,
            facts=[
                _fact("600001", D1, is_lu=True, streak=1),
                _fact("600001", D3, is_lu=True, streak=1),
            ],
        )
    )
    payload = await svc.get_stock_history(object(), days=20, min_streak=1, limit=300)  # type: ignore[arg-type]

    assert payload["as_of"] == D3
    assert payload["degraded_reason"] == "facts_gap"
    assert payload["missing_dates"] == [D2]
    assert payload["dates"] == [D1, D3]
    assert payload["window"] == {"days": 20, "start": D1, "end": D3, "samples": 2}
    assert len(payload["rows"][0]["cells"]) == 2
    assert fake.calls == [(D1, D3)]


@pytest.mark.asyncio
async def test_service_complete_window_has_no_degraded_reason(patch_repo) -> None:
    patch_repo(
        _FakeRepo(
            fact_date=D3,
            quote_date=D3,
            trade_dates=DATES,
            facts=[_fact("600001", d, is_lu=True, streak=i + 1) for i, d in enumerate(DATES)],
        )
    )
    payload = await svc.get_stock_history(object(), days=20, min_streak=2, limit=300)  # type: ignore[arg-type]
    assert payload["degraded_reason"] is None
    assert payload["missing_dates"] == []
    assert payload["window"]["samples"] == 3
    assert payload["rows"][0]["current_streak"] == 3


# ── 路由（直接调用端点函数，monkeypatch 服务）──────────────────────────────────


def _payload(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "as_of": D3,
        "window": {"days": 20, "start": D1, "end": D3, "samples": 3},
        "dates": DATES,
        "min_streak": 2,
        "total": 1,
        "truncated": False,
        "degraded_reason": None,
        "missing_dates": [],
        "rows": [
            {
                "symbol": "600001",
                "name": "测试股份",
                "current_status": "limit_up",
                "current_streak": 3,
                "max_streak": 3,
                "boards": 3,
                "broken_days": 0,
                "last_limit_up": D3,
                "cells": [{"status": "limit_up", "streak": 1}] * 3,
            }
        ],
    }
    base.update(over)
    return base


@pytest.mark.asyncio
async def test_route_serializes_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(db: Any, *, days: int, min_streak: int, limit: int) -> dict[str, Any]:
        assert (days, min_streak, limit) == (20, 2, 300)
        return _payload()

    monkeypatch.setattr(svc, "get_stock_history", fake_get)
    out = await api.get_limit_up_stock_history(db=object(), days=20, min_streak=2, limit=300)  # type: ignore[arg-type]

    assert isinstance(out, HeatStockHistoryOut)
    assert out.window.samples == 3
    assert len(out.rows[0].cells) == 3
    assert out.rows[0].cells[0].status == "limit_up"
    assert out.degraded_reason is None


@pytest.mark.asyncio
async def test_route_returns_200_model_for_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_get(db: Any, *, days: int, min_streak: int, limit: int) -> dict[str, Any]:
        return {
            "as_of": None,
            "window": {"days": days, "start": None, "end": None, "samples": 0},
            "dates": [],
            "min_streak": min_streak,
            "total": 0,
            "truncated": False,
            "degraded_reason": "no_fact_rows",
            "missing_dates": [],
            "rows": [],
        }

    monkeypatch.setattr(svc, "get_stock_history", fake_get)
    out = await api.get_limit_up_stock_history(db=object(), days=60, min_streak=0, limit=1000)  # type: ignore[arg-type]

    assert out.window.samples == 0
    assert out.rows == []
    assert out.degraded_reason == "no_fact_rows"
