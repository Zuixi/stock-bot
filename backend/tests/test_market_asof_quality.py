"""按日聚合端点必须带 as_of 质量标注，且 as_of 取自完整性判据而非 max(trade_date)。

服务层用 seam 替换判据与取行（`_*_rows`），不打库、不打网；`_NullDb` 只是占位
session —— 每个被触及的 DB 调用要么被 monkeypatch，要么根本不执行。
"""

from datetime import date
from typing import Any

import pytest

from app.services import limit_up_service, market_service
from app.services import market_day_service as mds

D15 = date(2026, 9, 15)
D16 = date(2026, 9, 16)
D17 = date(2026, 9, 17)


def _resolved(day: date = D16, quality: str = "fallback") -> mds.MarketDay:
    return mds.MarketDay(day, quality, "latest_day_incomplete", 5485, 5513, 1.0, True)  # type: ignore[arg-type]


def _patch_resolved_day(
    monkeypatch: pytest.MonkeyPatch, md: mds.MarketDay | None = _resolved()
) -> None:
    """Patch the completeness predicate at its single call site in market_service."""

    async def _resolve(_db: Any, *, cache: Any = None) -> mds.MarketDay | None:
        return md

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _resolve)


@pytest.fixture
def _day(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_resolved_day(monkeypatch)


# ---------------------------------------------------------------------------
# List endpoints: bare array -> {as_of, as_of_quality, as_of_reason, items}
# ---------------------------------------------------------------------------


async def test_distribution_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date) -> list[dict]:
        return [{"range": "0~1%", "count": 3}]

    monkeypatch.setattr(market_service, "_distribution_rows", _rows)
    out = await market_service.get_distribution(None)
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["as_of_reason"] == "latest_day_incomplete"
    assert out["items"] == [{"range": "0~1%", "count": 3}]


async def test_sectors_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date) -> list[dict]:
        return [{"name": "农林牧渔"}]

    monkeypatch.setattr(market_service, "_sector_rows", _rows)
    out = await market_service.get_sectors(None)
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["items"] == [{"name": "农林牧渔"}]


async def test_capital_flow_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date) -> list[dict]:
        return [{"name": "电子", "inflow": 1.0, "outflow": -1.0}]

    monkeypatch.setattr(market_service, "_capital_flow_rows", _rows)
    out = await market_service.get_capital_flow(None)
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["items"][0]["name"] == "电子"


async def test_hot_boards_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date, _category: str) -> list[dict]:
        return [{"id": "industry-电子", "name": "电子"}]

    monkeypatch.setattr(market_service, "_hot_board_rows", _rows)
    out = await market_service.get_hot_boards("industry", None)
    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["items"][0]["name"] == "电子"


async def test_hot_boards_concept_is_empty_envelope(monkeypatch) -> None:
    async def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("concept has no data source; must not probe the DB")

    monkeypatch.setattr(market_service.market_day_service, "resolve_latest_complete_day", _boom)
    out = await market_service.get_hot_boards("concept", None)
    assert out == {"as_of": None, "as_of_quality": "partial", "as_of_reason": None, "items": []}


async def test_empty_db_list_endpoint_never_raises(monkeypatch) -> None:
    """无任何行情 → as_of=None / partial / items=[]；不抛异常、不缓存。"""
    _patch_resolved_day(monkeypatch, None)

    async def _boom(*_a: Any, **_kw: Any) -> list[dict]:
        raise AssertionError("no resolved day => row loader must not run")

    monkeypatch.setattr(market_service, "_distribution_rows", _boom)
    out = await market_service.get_distribution(None)
    assert out == {"as_of": None, "as_of_quality": "partial", "as_of_reason": None, "items": []}


# ---------------------------------------------------------------------------
# Object-shaped endpoints: rankings / sw-performance gain the quality fields
# ---------------------------------------------------------------------------


async def test_rankings_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _rank_type: str, _day_: date, _limit: int) -> list[dict]:
        return []

    monkeypatch.setattr(market_service, "_ranking_rows", _rows)
    out = await market_service.get_rankings(None, None, "gainers", 5)
    assert out.as_of == D16
    assert out.as_of_quality == "fallback"
    assert out.as_of_reason == "latest_day_incomplete"


async def test_sw_performance_carries_as_of_quality(_day: None, monkeypatch) -> None:
    async def _rows(_db: Any, _day_: date) -> list[dict]:
        return []

    monkeypatch.setattr(market_service, "_sw_performance_rows", _rows)
    out = await market_service.get_sw_industry_performance(None, None, 31)
    assert out.as_of == D16
    assert out.as_of_quality == "fallback"
    assert out.as_of_reason == "latest_day_incomplete"


# ---------------------------------------------------------------------------
# Limit-up snapshot: resolved day (and its quality) replaces max(trade_date)
# ---------------------------------------------------------------------------


def _patch_snapshot_sources(monkeypatch: pytest.MonkeyPatch, *, limits: bool = False) -> None:
    async def _dates(_db: Any, as_of: date, limit: int) -> list[date]:
        return [D15, D16]

    async def _breadth(_db: Any, day: date) -> dict[str, int]:
        return {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0}

    async def _has(_db: Any, day: date) -> bool:
        return limits

    monkeypatch.setattr(limit_up_service.limit_up_repo, "list_recent_trade_dates", _dates)
    monkeypatch.setattr(limit_up_service.limit_up_repo, "fetch_day_breadth", _breadth)
    monkeypatch.setattr(limit_up_service.limit_up_repo, "has_price_limits", _has)


async def test_limit_up_snapshot_uses_resolved_day_and_quality(monkeypatch) -> None:
    async def _resolve(_db: Any, *, cache: Any = None) -> mds.MarketDay:
        return _resolved()

    monkeypatch.setattr(
        limit_up_service.market_day_service, "resolve_latest_complete_day", _resolve
    )
    _patch_snapshot_sources(monkeypatch, limits=False)

    snap = await limit_up_service.get_snapshot(None)

    assert snap["as_of"] == D16
    assert snap["as_of_quality"] == "fallback"


async def test_limit_up_snapshot_explicit_as_of_keeps_exact_day(monkeypatch) -> None:
    """显式 ?date= 保持精确日语义：不得回落到判据，质量按 partial 标注。"""

    async def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("explicit as_of must not consult the completeness predicate")

    monkeypatch.setattr(limit_up_service.market_day_service, "resolve_latest_complete_day", _boom)
    _patch_snapshot_sources(monkeypatch, limits=False)

    snap = await limit_up_service.get_snapshot(None, as_of=D17)

    assert snap["as_of"] == D17
    assert snap["as_of_quality"] == "partial"


async def test_limit_up_empty_payload_carries_partial_quality(monkeypatch) -> None:
    async def _none(_db: Any) -> None:
        return None

    monkeypatch.setattr(limit_up_service.limit_up_repo, "latest_quote_date", _none)
    snap = await limit_up_service.get_snapshot(None)
    assert snap["as_of"] is None
    assert snap["as_of_quality"] == "partial"
    assert snap["degraded_reason"] == "no_quotes"


async def test_limit_up_projections_carry_as_of_quality(monkeypatch) -> None:
    snap = {
        "as_of": D16,
        "as_of_prev": D15,
        "source": "local_calc",
        "degraded_reason": "price_limits_missing",
        "as_of_quality": "fallback",
        "sectors": {"items": [], "unclassified_count": 0},
        "yesterday": {"kpis": {}, "items": []},
    }
    assert limit_up_service.sector_payload(snap)["as_of_quality"] == "fallback"
    assert limit_up_service.yesterday_payload(snap)["as_of_quality"] == "fallback"
