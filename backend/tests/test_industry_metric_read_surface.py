"""spec §S1.2 通用指标读面：纯单测（不触 DB / 不触 Redis）。

覆盖三块：
1. `range` 语法 → 行数上限（`range_limit`，含钳制与非法值）
2. registry 的 metric_key 白名单与口径版本登记位（未登记即拒绝）
3. 四徽章投影（`_latest_out`）—— 尤其 `published_at` 为 NULL 时 as_of 回退 period
   但 `published_at` 字段本身必须仍是 None（不许把回退值写回 PIT 字段）
4. 路由层：db/cache 依赖直调（不走 TestClient，不起服务）—— 未登记 key → 404、
   非法 range → 404、正常路径四徽章齐备、series 命中缓存键含换算后的 limit
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from fastapi import HTTPException

from app.models.industry_research import IndustryMetric
from app.schemas.industry import MetricSeriesOut
from app.services import industry_metric_service as service
from app.services.industry_registry import (
    INDUSTRIES,
    PIG_INDUSTRY,
    MetricDef,
    find_metric,
)

# ── 1) range 语法 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("range_key", "expected"),
    [
        ("30d", 30),
        ("6w", 42),
        ("3m", 93),
        ("1y", 366),
        ("all", service.SERIES_MAX_LIMIT),
        ("ALL", service.SERIES_MAX_LIMIT),  # 大小写不敏感
        ("1Y", 366),
    ],
)
def test_range_limit_parses_units(range_key: str, expected: int) -> None:
    assert service.range_limit(range_key) == expected


@pytest.mark.parametrize("range_key", ["", "y", "1", "1x", "-3d", "0d", "1.5y", "1y2m"])
def test_range_limit_rejects_garbage(range_key: str) -> None:
    assert service.range_limit(range_key) is None


def test_range_limit_clamps_to_max() -> None:
    """`999y` 不许把全表拖出来（上界钳制）。"""
    assert service.range_limit("999y") == service.SERIES_MAX_LIMIT


def test_series_default_range_is_valid() -> None:
    assert service.range_limit(service.SERIES_DEFAULT_RANGE) is not None


# ── 2) registry 白名单 + 口径版本登记位 ────────────────────────────────


def test_find_metric_returns_owner_scope_and_def() -> None:
    found = find_metric("hog_price")
    assert found is not None
    scope_key, m = found
    assert scope_key == "pig"
    assert m.name == "生猪均价"
    assert m.freq == "daily"


def test_find_metric_rejects_unregistered_key() -> None:
    """未登记 metric_key 必须被 registry 拒绝（spec §S1.2 验收判据）。"""
    assert find_metric("does_not_exist") is None
    assert find_metric("") is None


def test_every_metric_def_has_caliber_version_slot() -> None:
    """口径版本登记位存在：本期不预填具体口径，故全部为 None。"""
    for cfg in INDUSTRIES.values():
        for m in cfg.metrics:
            assert hasattr(m, "caliber_version")
            assert m.caliber_version is None  # 只要求能力存在，不回填口径


def test_metric_def_caliber_version_is_settable() -> None:
    m = MetricDef(
        key="x",
        name="x",
        unit="",
        freq="daily",
        tier="manual",
        sources=["manual"],
        caliber_version="M2-2026",
    )
    assert m.caliber_version == "M2-2026"
    # 默认 None，不破坏既有 MetricDef(...) 位置参数调用
    assert MetricDef("y", "y", "", "daily", "manual", ["manual"]).caliber_version is None


# ── 3) 四徽章投影 ─────────────────────────────────────────────────────


def _row(*, published_at: datetime | None, caliber: str | None = None) -> IndustryMetric:
    return IndustryMetric(
        industry_key="pig",
        stock_id=0,
        metric_key="hog_price",
        source="akshare_soozhu",
        source_tier="highfreq",
        freq="daily",
        period=date(2026, 9, 30),
        value=13.85,
        unit="元/kg",
        published_at=published_at,
        caliber_version=caliber,
    )


def test_latest_out_carries_four_badges() -> None:
    m = PIG_INDUSTRY.metric("hog_price")
    assert m is not None
    published = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)
    out = service._latest_out(m, _row(published_at=published, caliber="M1"), row_scope="pig")
    assert out.source == "akshare_soozhu"
    assert out.freq == "daily"
    assert out.as_of == date(2026, 10, 8)  # PIT 徽章取实际可得时点
    assert out.published_at == published
    assert out.caliber_version == "M1"
    assert out.caliber_version_registered is None  # registry 尚未回填口径
    assert out.scope_key == "pig"


def test_latest_out_falls_back_as_of_but_not_pit_field() -> None:
    """`published_at` NULL：as_of 回退 period，但 published_at 仍为 None。

    这是"不许静默回退"的读面表达：消费方能分辨"真 PIT"与"回退值"。
    """
    m = PIG_INDUSTRY.metric("hog_price")
    assert m is not None
    out = service._latest_out(m, _row(published_at=None), row_scope="pig")
    assert out.as_of == date(2026, 9, 30)
    assert out.published_at is None


def test_latest_out_without_row_is_a_skeleton() -> None:
    m = PIG_INDUSTRY.metric("hog_price")
    assert m is not None
    out = service._latest_out(m, None)
    assert out.metric_key == "hog_price"
    assert out.value is None
    assert out.as_of is None
    assert out.published_at is None
    assert out.caliber_version_registered is None


def test_caliber_of_surfaces_revision_break() -> None:
    """行上口径与 registry 当前口径不一致 = 上游口径断点（消费方据此提示）。"""
    m = MetricDef("x", "x", "", "daily", "manual", ["manual"], caliber_version="M2")
    stored, registered = service._caliber_of(m, _row(published_at=None, caliber="M1"))
    assert (stored, registered) == ("M1", "M2")


# ── 4) 路由层（直调 handler） ──────────────────────────────────────────


class _NoneCache:
    """cache 替身：永远 miss，记录 set。"""

    def __init__(self) -> None:
        self.gets: list[str] = []
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any | None:  # noqa: ANN401
        self.gets.append(key)
        return None

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))


async def test_latest_endpoint_returns_four_badges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import metrics as api

    async def _fake(
        _db: Any, scope_keys: list[str] | None = None, metric_keys: list[str] | None = None
    ) -> list[Any]:
        assert scope_keys == ["pig"]
        assert metric_keys == ["hog_price"]
        m = PIG_INDUSTRY.metric("hog_price")
        assert m is not None
        return [
            service._latest_out(
                m,
                _row(published_at=datetime(2026, 10, 8, tzinfo=UTC), caliber="M1"),
                row_scope="pig",
            )
        ]

    monkeypatch.setattr(service, "get_latest_metrics_across_scopes", _fake)
    out = await api.get_latest_metrics(db=None, scope_keys=["pig"], metric_keys=["hog_price"])  # type: ignore[arg-type]
    body = out[0]
    assert {"source", "as_of", "freq", "caliber_version"} <= set(body.model_dump())
    assert body.source == "akshare_soozhu"
    assert body.as_of == date(2026, 10, 8)
    assert body.freq == "daily"
    assert body.caliber_version == "M1"


async def test_latest_endpoint_maps_unregistered_metric_to_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import metrics as api

    async def _boom(*_a: Any, **_kw: Any) -> list[Any]:
        raise service.UnknownMetricError("Metric 'nope' is not registered in registry")

    monkeypatch.setattr(service, "get_latest_metrics_across_scopes", _boom)
    with pytest.raises(HTTPException) as exc:
        await api.get_latest_metrics(db=None, scope_keys=None, metric_keys=["nope"])  # type: ignore[arg-type]
    assert exc.value.status_code == 404
    assert "nope" in str(exc.value.detail)


async def test_series_endpoint_rejects_bad_range_before_touching_db() -> None:
    from app.api.v1 import metrics as api

    with pytest.raises(HTTPException) as exc:
        await api.get_series(
            db=None, cache=_NoneCache(), scope_key="pig", metric_key="hog_price", range="weekly"
        )  # type: ignore[arg-type]
    assert exc.value.status_code == 404


async def test_series_endpoint_caches_with_canonical_limit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缓存键用换算后的 limit → `1y` 与 `366d` 同键（不会各存一份）。"""
    from app.api.v1 import metrics as api

    async def _fake(_db: Any, scope_key: str, metric_key: str, range_key: str) -> MetricSeriesOut:
        return MetricSeriesOut(
            scope_key=scope_key,
            metric_key=metric_key,
            name="生猪均价",
            unit="元/kg",
            tier="highfreq",
            freq="daily",
            source="mock",
            range=range_key,
            limit=service.range_limit(range_key) or 0,
        )

    monkeypatch.setattr(service, "get_series", _fake)
    cache = _NoneCache()
    for range_key in ("1y", "366d"):
        await api.get_series(
            db=None, cache=cache, scope_key="pig", metric_key="hog_price", range=range_key
        )  # type: ignore[arg-type]
    assert [k for k, _v, _t in cache.sets] == [
        "metrics:series:pig:hog_price:366",
        "metrics:series:pig:hog_price:366",
    ]
    assert all(ttl == service.SERIES_CACHE_TTL for _k, _v, ttl in cache.sets)


async def test_series_endpoint_returns_cached_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.api.v1 import metrics as api

    async def _never(*_a: Any, **_kw: Any) -> MetricSeriesOut:
        raise AssertionError("cache hit 时不得回源 DB")

    monkeypatch.setattr(service, "get_series", _never)
    cached = MetricSeriesOut(
        scope_key="pig",
        metric_key="hog_price",
        name="生猪均价",
        freq="daily",
        range="1y",
        limit=366,
    )

    class _HitCache(_NoneCache):
        async def get(self, key: str) -> Any:  # noqa: ANN401
            return cached.model_dump(mode="json")

    out = await api.get_series(
        db=None, cache=_HitCache(), scope_key="pig", metric_key="hog_price", range="1y"
    )  # type: ignore[arg-type]
    assert out.metric_key == "hog_price"
    assert out.limit == 366
