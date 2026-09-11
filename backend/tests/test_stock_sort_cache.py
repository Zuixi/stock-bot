"""Sort path of list_stocks_enriched must be Redis-cached (public homepage protection)."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.schemas.stock import StockEnrichedOut
from app.services import stock_service


class RecordingCache:
    """In-memory CacheClient double：get/set 契约与 app.core.redis.CacheClient 一致。"""

    def __init__(self) -> None:
        self.store: dict[str, object] = {}
        self.get_calls = 0

    async def get(self, key: str):
        self.get_calls += 1
        return self.store.get(key)

    async def set(self, key: str, value: object, ttl: int | None = None) -> None:
        self.store[key] = value


def _params(**overrides) -> SimpleNamespace:
    base = {
        "sort_by": "changePercent",
        "sort_order": "desc",
        "exchange": None,
        "category": None,
        "keyword": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _stock() -> SimpleNamespace:
    return SimpleNamespace(id=1, symbol="600000", name="浦发银行", exchange="Shanghai_Stocks")


def _enriched() -> StockEnrichedOut:
    # 生产路径 get_stocks_enriched_by_symbols 返回 list[StockEnrichedOut]；
    # 缓存写入会调用 model_dump，替身必须用真实 schema 才能覆盖序列化契约。
    return StockEnrichedOut(
        id=1,
        exchange="Shanghai_Stocks",
        symbol="600000",
        name="浦发银行",
        category="",
        asof=datetime(2026, 9, 11),
        latest_price=10.0,
        change_percent=1.5,
        amount=100.0,
    )


def _patch(monkeypatch, repo_calls: dict[str, int]) -> None:
    async def _list_stocks(_db, _p, offset, limit):
        repo_calls["n"] += 1
        return [_stock()], 1

    async def _enrich(_db, _symbols):
        return [_enriched()]

    monkeypatch.setattr(stock_service.stock_repo, "list_stocks", _list_stocks)
    monkeypatch.setattr("app.services.market_service.get_stocks_enriched_by_symbols", _enrich)


@pytest.mark.asyncio
async def test_sort_path_reads_and_writes_cache(monkeypatch) -> None:
    repo_calls = {"n": 0}
    _patch(monkeypatch, repo_calls)
    cache = RecordingCache()
    db = AsyncMock()
    page = SimpleNamespace(offset=0, page_size=10)

    out1, total1 = await stock_service.list_stocks_enriched(db, cache, _params(), page)
    out2, total2 = await stock_service.list_stocks_enriched(db, cache, _params(), page)

    assert total1 == total2 == 1
    assert len(out1) == len(out2) == 1
    assert repo_calls["n"] == 1, "第二次同参调用必须命中缓存，不得再查库"
    assert cache.get_calls == 2


@pytest.mark.asyncio
async def test_sort_cache_key_separates_filter_dimensions(monkeypatch) -> None:
    """不同 keyword / category 必须各自落键，带筛选的排序结果不得互串。"""
    repo_calls = {"n": 0}
    _patch(monkeypatch, repo_calls)
    cache = RecordingCache()
    db = AsyncMock()
    page = SimpleNamespace(offset=0, page_size=10)

    await stock_service.list_stocks_enriched(db, cache, _params(keyword="a"), page)
    await stock_service.list_stocks_enriched(db, cache, _params(keyword="b"), page)
    await stock_service.list_stocks_enriched(db, cache, _params(keyword="a"), page)
    await stock_service.list_stocks_enriched(db, cache, _params(keyword="a", category="金融"), page)
    await stock_service.list_stocks_enriched(db, cache, _params(keyword="a"), page)

    # 5 次调用中命中 2 次（第 3、5 次），落库 3 次：keyword=a / keyword=b / (a+金融)
    assert repo_calls["n"] == 3
    assert cache.get_calls == 5
