"""P7 溯源端点契约：未知指标 404 / 缓存命中零查询 / 缺 scope 404."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi import HTTPException

from app.api.v1 import provenance as api


class _Cache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.sets = 0

    async def get(self, key: str) -> Any | None:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets += 1
        self.store[key] = value


async def test_unknown_metric_is_404() -> None:
    with pytest.raises(HTTPException) as exc:
        await api.get_provenance(
            "not-a-metric", db=object(), cache=_Cache(), date_=date(2026, 11, 10), scope_key=None
        )
    assert exc.value.status_code == 404


async def test_scope_required_metric_without_scope_is_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Any] = []

    async def _build(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return None

    monkeypatch.setattr(api.provenance_service, "build", _build)
    with pytest.raises(HTTPException) as exc:
        await api.get_provenance(
            "sw_l1_pct_chg", db=object(), cache=_Cache(), date_=date(2026, 11, 10), scope_key=None
        )
    assert exc.value.status_code == 404
    assert calls == []  # 提前拒绝，连 service 都不进


async def test_cache_hit_skips_build(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache()
    payload = {"metric": {"key": "zt_count"}, "value": {"value": 95}}
    cache.store["market:provenance:zt_count:2026-11-10:-"] = payload

    async def _build(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("cache hit 不应再查库")

    monkeypatch.setattr(api.provenance_service, "build", _build)
    out = await api.get_provenance(
        "zt_count", db=object(), cache=cache, date_=date(2026, 11, 10), scope_key=None
    )
    assert out == payload


async def test_success_writes_cache_with_date_in_key(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = _Cache()

    async def _build(*args: Any, **kwargs: Any) -> Any:
        return {"metric": {"key": "zt_count"}, "value": {"value": 1}}

    monkeypatch.setattr(api.provenance_service, "build", _build)
    out = await api.get_provenance(
        "zt_count", db=object(), cache=cache, date_=date(2026, 11, 10), scope_key=None
    )
    assert out["value"]["value"] == 1
    assert cache.sets == 1
    assert "2026-11-10" in next(iter(cache.store))


class _OneRow:
    def __init__(self, row: tuple[Any, ...]) -> None:
        self._row = row

    def first(self) -> tuple[Any, ...]:
        return self._row


class _FakeDb:
    async def execute(self, *_args: Any, **_kwargs: Any) -> _OneRow:
        return _OneRow((13.55, date(2026, 8, 31), None, "manual", "M1-2026"))


async def test_registered_metric_without_ledger_is_definition_only() -> None:
    """已登记但没接批次账本的指标：给定义+可见值，明确标注 definition_only。

    不能因为没接 ledger 就 404（用户仍应能问"这个数从哪来"），也不能伪造
    batch/quality（那会让溯源变成更精致的谎言）。
    """
    out = await api.get_provenance(
        "industry_cost_avg",
        db=_FakeDb(),
        cache=_Cache(),
        date_=date(2026, 11, 10),
        scope_key="pig",
    )
    assert out["lineage_status"] == "definition_only"
    assert out["value"]["value"] == pytest.approx(13.55)
    assert out["sources"][0]["table"] == "industry_metrics"
    assert out["sources"][0]["batch_id"] is None
    assert out["quality"] == []
