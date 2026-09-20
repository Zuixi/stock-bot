"""按日聚合端点必须带 as_of 质量标注，且 as_of 取自完整性判据而非 max(trade_date)。

服务层用 seam 替换判据与取行，不打库、不打网：按日聚合的四个端点自 Task 7 起共用
``market_snapshot_service.load_day_rows``（本文件 patch 它），SW 业绩仍走自家的
``_sw_performance_rows``；``None`` 只是占位 session —— 每个被触及的 DB 调用要么被
monkeypatch，要么根本不执行。
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


def _patch_eastmoney_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Force ``get_hot_boards`` onto its local fallback (East Money unavailable)."""

    def _boom() -> Any:
        raise RuntimeError("eastmoney unavailable (patched)")

    monkeypatch.setattr(market_service, "get_eastmoney_client", _boom)


def _patch_day_rows(monkeypatch: pytest.MonkeyPatch, rows: list[dict]) -> None:
    """Patch the shared per-day snapshot loader (Task 7) used by the market plane."""

    async def _load(_db: Any, _day_: date, *, cache: Any = None) -> list[dict]:
        return rows

    monkeypatch.setattr(market_service, "load_day_rows", _load)


async def test_distribution_carries_as_of_quality(_day: None, monkeypatch) -> None:
    _patch_day_rows(monkeypatch, [{"pct_chg": 0.5}, {"pct_chg": 0.5}, {"pct_chg": 0.5}])

    out = await market_service.get_distribution(None)

    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["as_of_reason"] == "latest_day_incomplete"
    buckets = {item["range"]: item["count"] for item in out["items"]}
    assert buckets["0~1%"] == 3
    assert sum(buckets.values()) == 3


async def test_sectors_carries_as_of_quality(_day: None, monkeypatch) -> None:
    _patch_day_rows(monkeypatch, [{"csrc_desc": "农林牧渔", "pct_chg": 1.0, "amount": 1.0}])

    out = await market_service.get_sectors(None)

    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert [item["name"] for item in out["items"]] == ["农林牧渔"]


async def test_capital_flow_carries_as_of_quality(_day: None, monkeypatch) -> None:
    _patch_day_rows(monkeypatch, [{"csrc_desc": "电子", "pct_chg": 1.0, "amount": 100000.0}])

    out = await market_service.get_capital_flow(None)

    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["items"][0] == {"name": "电子", "inflow": 1.0, "outflow": -0.0}


async def test_hot_boards_fallback_carries_resolved_day_as_of_quality(
    _day: None, monkeypatch
) -> None:
    """Task 14: East Money down => the local fallback keeps the T+1 day semantics
    (``as_of`` = 判据日) and marks itself; the East Money path reports *today* as
    ``partial`` instead — see ``test_board_endpoints.py``."""
    _patch_day_rows(monkeypatch, [{"csrc_desc": "电子", "pct_chg": 1.0}])
    _patch_eastmoney_failure(monkeypatch)

    out = await market_service.get_hot_boards("industry", None)

    assert out["as_of"] == "2026-09-16"
    assert out["as_of_quality"] == "fallback"
    assert out["items"][0]["id"] == "industry-电子"
    assert (out["source"], out["degraded_reason"]) == ("local_grouping", "eastmoney_unavailable")


# ``test_hot_boards_concept_is_empty_envelope`` locked "concept has no data source"
# (concept must not even probe the DB). Task 14 wired concept to the East Money
# concept board set (``m:90+t:3``), so an empty concept envelope is now a bug: the
# concept contract (non-empty BK-coded items, still zero DB reads) is locked by
# ``test_board_endpoints.py``.


async def test_empty_db_list_endpoint_never_raises(monkeypatch) -> None:
    """无任何行情 → as_of=None / partial / items=[]；不抛异常、不缓存。"""
    _patch_resolved_day(monkeypatch, None)

    async def _boom(*_a: Any, **_kw: Any) -> list[dict]:
        raise AssertionError("no resolved day => row loader must not run")

    monkeypatch.setattr(market_service, "load_day_rows", _boom)
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


class _SeededCache:
    """One preset payload for any key; records the keys asked for (no redis, no mock lib)."""

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self.payload = payload
        self.get_keys: list[str] = []

    async def get(self, key: str) -> Any | None:
        self.get_keys.append(key)
        return self.payload

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:  # pragma: no cover
        raise AssertionError("cache-hit paths must not write")


_SNAPSHOT_BODY: dict[str, Any] = {
    "as_of": D16,
    "as_of_prev": D15,
    "source": "local_calc",
    "limits_present": True,
    "is_partial": False,
    "sw_coverage": None,
    "degraded_reason": None,
    "market_days": [D15, D16],
    "breadth": {"zt_count": 0, "dt_count": 0, "zb_count": 0, "quoted": 0},
    "kpis": {},
    "echelons": [],
    "sectors": {"items": [], "unclassified_count": 0},
    "yesterday": {"kpis": {}, "items": []},
}


async def test_snapshot_cache_hit_relabels_with_the_resolved_quality(monkeypatch) -> None:
    """显式 ?date= 写下的 "partial" 标签不得污染后续默认请求。

    快照体只由 (target, lookback) 决定，quality 是**逐请求**标签：显式日期请求先
    写缓存、默认请求后命中时，必须用本次解析出的 quality 覆盖缓存里的旧标签。
    """

    async def _resolve(_db: Any, *, cache: Any = None) -> mds.MarketDay:
        return mds.MarketDay(D16, "complete", None, 5485, 5513, 1.0, True)

    monkeypatch.setattr(
        limit_up_service.market_day_service, "resolve_latest_complete_day", _resolve
    )
    cache = _SeededCache({**_SNAPSHOT_BODY, "as_of_quality": "partial"})

    snap = await limit_up_service.get_snapshot(cache)  # type: ignore[arg-type]

    assert snap["as_of_quality"] == "complete"
    assert snap["as_of"] == D16
    assert cache.get_keys == [
        f"market:limit-up:snapshot:{D16.isoformat()}:{limit_up_service.calc.LOOKBACK_TRADE_DAYS}"
    ]


async def test_snapshot_explicit_date_cache_hit_labels_partial(monkeypatch) -> None:
    """反向：默认请求写下的 "complete" 也不得让显式 ?date= 谎报完整度。"""

    async def _boom(*_a: Any, **_kw: Any) -> None:
        raise AssertionError("explicit as_of must not consult the completeness predicate")

    monkeypatch.setattr(limit_up_service.market_day_service, "resolve_latest_complete_day", _boom)
    cache = _SeededCache({**_SNAPSHOT_BODY, "as_of_quality": "complete"})

    snap = await limit_up_service.get_snapshot(cache, as_of=D16)  # type: ignore[arg-type]

    assert snap["as_of"] == D16
    assert snap["as_of_quality"] == "partial"
