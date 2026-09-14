"""快照编排的降级契约（monkeypatch，无 DB/网络）。

source 由数据可用性决定，不由偏好决定；限价缺失时返回空 payload + 原因，
绝不输出按比例猜出来的涨停——详见 docs/design/limit-up-sentiment.md §5。
"""

from datetime import date
from typing import Any

import pytest

from app.services import limit_up_service as svc

D5 = date(2026, 9, 8)
D4 = date(2026, 9, 7)


@pytest.fixture
def _patch_sources(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    state: dict[str, Any] = {
        "limits": True,
        "window": [],
        "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
        "trade_dates": [date(2026, 8, 18), D4, D5],
    }

    async def _latest(_db: Any) -> date:
        return D5

    async def _dates(_db: Any, _as_of: date, _limit: int) -> list[date]:
        return state["trade_dates"]

    async def _has(_db: Any, _as_of: date) -> bool:
        return bool(state["limits"])

    async def _window(_db: Any, **_kw: Any) -> list[dict[str, Any]]:
        return list(state["window"])

    async def _breadth(_db: Any, _as_of: date) -> dict[str, int]:
        return dict(state["breadth"])

    monkeypatch.setattr(svc.limit_up_repo, "latest_quote_date", _latest)
    monkeypatch.setattr(svc.limit_up_repo, "list_recent_trade_dates", _dates)
    monkeypatch.setattr(svc.limit_up_repo, "has_price_limits", _has)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_limit_up_window", _window)
    monkeypatch.setattr(svc.limit_up_repo, "fetch_day_breadth", _breadth)
    return state


async def test_missing_price_limits_degrades_without_guessing(
    _patch_sources: dict[str, Any],
) -> None:
    _patch_sources["limits"] = False
    _patch_sources["breadth"] = {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 5490}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["source"] == "local_calc"
    assert snap["limits_present"] is False
    assert snap["degraded_reason"] == "price_limits_missing"
    assert snap["echelons"] == [] and snap["yesterday"]["items"] == []


async def test_partial_day_is_flagged_not_hidden(_patch_sources: dict[str, Any]) -> None:
    _patch_sources["breadth"] = {"zt_count": 3, "dt_count": 0, "zb_count": 1, "quoted": 12}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["is_partial"] is True
    assert snap["degraded_reason"] == "partial_day"


async def test_complete_day_reports_local_calc(_patch_sources: dict[str, Any]) -> None:
    _patch_sources["breadth"] = {"zt_count": 75, "dt_count": 1, "zb_count": 39, "quoted": 5490}
    snap = await svc.get_snapshot(cache=None, as_of=D5)
    assert snap["source"] == "local_calc"
    assert snap["limits_present"] is True and snap["is_partial"] is False
    assert snap["degraded_reason"] is None
    assert snap["kpis"]["zt_count"] == 75 and snap["kpis"]["zb_count"] == 39


async def test_empty_daily_quotes_returns_empty_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _none(_db: Any) -> None:
        return None

    monkeypatch.setattr(svc.limit_up_repo, "latest_quote_date", _none)
    snap = await svc.get_snapshot(cache=None)
    assert snap["degraded_reason"] == "no_quotes"
    assert snap["as_of"] is None
