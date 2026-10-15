"""spec §S1.2 通用指标读面：纯单测（不触 DB / 不触 Redis）。

覆盖四块：
1. `range` 语法 → **真实时间窗**（`parse_series_range`：窗口天数 + 规范化文本 + 上界钳制）
2. registry 的 metric_key 白名单与口径版本登记位（未登记即拒绝）
3. 四徽章投影（`_latest_out`）—— `published_at` 为 NULL 时 as_of 回退 period，但
   **必须**伴随 `as_of_source == "period"`（Ruling 34：不静默回退）；有 `published_at`
   时按 Asia/Shanghai 取日历日（Ruling 35）；`published_at` 字段本身保持 None
4. 路由层：db/cache 依赖直调（不走 TestClient，不起服务）—— 未登记 key → 404、
   非法 range / 空 metric_keys → 422、正常路径四徽章齐备、缓存键按规范化 range
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
    ("range_key", "days", "canonical"),
    [
        ("30d", 30, "30d"),
        ("6w", 42, "42d"),
        ("3m", 93, "93d"),
        ("1y", 366, "366d"),
        ("1Y", 366, "366d"),  # 大小写不敏感
        ("366d", 366, "366d"),  # 与 1y 是同一个窗口 → 同一 canonical 文本
    ],
)
def test_parse_series_range_window_and_canonical_text(
    range_key: str, days: int, canonical: str
) -> None:
    """range 是**时间窗**：窗口天数与规范化文本（缓存键/响应都用后者）。"""
    parsed = service.parse_series_range(range_key)
    assert parsed is not None
    assert parsed.days == days
    assert parsed.text == canonical


def test_parse_series_range_all_has_no_window() -> None:
    parsed = service.parse_series_range("ALL")
    assert parsed is not None
    assert parsed.days is None and parsed.text == "all"
    assert parsed.limit == service.SERIES_MAX_LIMIT
    assert parsed.period_from(date(2026, 10, 12)) is None  # 无窗 = 不设下界


def test_parse_series_range_period_from_is_calendar_window() -> None:
    parsed = service.parse_series_range("30d")
    assert parsed is not None
    assert parsed.period_from(date(2026, 10, 12)) == date(2026, 9, 12)


@pytest.mark.parametrize("range_key", ["", "y", "1", "1x", "-3d", "0d", "1.5y", "1y2m"])
def test_parse_series_range_rejects_garbage(range_key: str) -> None:
    assert service.parse_series_range(range_key) is None


@pytest.mark.parametrize("range_key", ["100000y", "999999999999y", "1000y"])
def test_parse_series_range_rejects_absurd_windows(range_key: str) -> None:
    """Ruling 36：超大窗口必须**判非法**（调用方 422），绝不让 `date - timedelta` 溢出。

    修复前 `100000y` → `days = 36_600_000` → `OverflowError`；`999999999999y` →
    `timedelta` 自身抛 OverflowError —— 异常穿透路由变成 HTTP 500。
    """
    assert service.parse_series_range(range_key) is None


def test_parse_series_range_accepts_boundary_window() -> None:
    """上限内的最大窗口可用（`999y`），且 `period_from` 真能算出来（不溢出）。"""
    parsed = service.parse_series_range("999y")
    assert parsed is not None
    assert parsed.days == service.SERIES_MAX_DAYS
    assert parsed.period_from(date(2026, 10, 12)) == date(2026, 10, 12) - timedelta(
        days=service.SERIES_MAX_DAYS
    )


def test_parse_series_range_limit_aligns_with_inclusive_window() -> None:
    """`limit = days + 1`：窗口下界含当天，日频在窗内最多 days+1 个点（F3 差一）。"""
    parsed = service.parse_series_range("30d")
    assert parsed is not None
    assert parsed.days == 30 and parsed.limit == 31


def test_parse_series_range_clamps_limit_to_max() -> None:
    """`999y` 不许把全表拖出来（行数上界钳制），但窗口天数照实记。"""
    parsed = service.parse_series_range("999y")
    assert parsed is not None
    assert parsed.limit == service.SERIES_MAX_LIMIT
    assert parsed.days == 999 * 366


def test_series_default_range_is_valid() -> None:
    assert service.parse_series_range(service.SERIES_DEFAULT_RANGE) is not None


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
    """每个登记项都有口径版本登记位，且类型是 str | None（**不断言全为 None**）。

    断言"全为 None"是个时间炸弹：任何后续任务按 registry:73 的口径修订要求合法声明
    ``caliber_version="M1-2026"`` 都会把它打红。这里只锁能力与类型。
    """
    for cfg in INDUSTRIES.values():
        for m in cfg.metrics:
            assert hasattr(m, "caliber_version")
            assert m.caliber_version is None or isinstance(m.caliber_version, str)


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
    assert out.as_of == date(2026, 10, 9)  # 上海时间 10-09 00:30（见下一个测试）
    assert out.as_of_source == service.AS_OF_FROM_PUBLISHED  # 真 PIT，判别式明确
    assert out.published_at == published
    assert out.caliber_version == "M1"
    assert out.caliber_version_registered is None  # registry 尚未回填口径
    assert out.scope_key == "pig"


def test_as_of_uses_shanghai_calendar_not_utc() -> None:
    """Ruling 35：`published_at` 的"实际可得日"按 Asia/Shanghai 判定。

    输入构造成 **UTC 2026-10-08 16:30**（= 北京 2026-10-09 00:30）——旧实现
    `row.published_at.date()` 取 UTC 日历日会得到 10-08（比真实可得日早一天 =
    回测偷看未来），故去掉 `astimezone(_CN_TZ)` 时本用例必然变红。
    （上一版输入自带 `+08:00`，两种实现都给 10-09，是空断言 —— 已按 F6 重建。）
    """
    utc_instant = datetime(2026, 10, 8, 16, 30, tzinfo=UTC)
    assert utc_instant.date() == date(2026, 10, 8)  # 旧实现会给出的（错误）值
    as_of, source = service._pit_as_of(_row(published_at=utc_instant))
    assert as_of == date(2026, 10, 9)
    assert source == service.AS_OF_FROM_PUBLISHED


def test_latest_out_falls_back_as_of_with_explicit_discriminant() -> None:
    """`published_at` NULL：as_of 回退 period，但**必须**带 `as_of_source="period"`。

    ADR 0011「绝不静默回退到 period」的读面表达：值仍有用于展示，回退事实显式标注，
    规则/回测路径（S2.2）据此拒绝。
    """
    m = PIG_INDUSTRY.metric("hog_price")
    assert m is not None
    out = service._latest_out(m, _row(published_at=None), row_scope="pig")
    assert out.as_of == date(2026, 9, 30)
    assert out.as_of_source == service.AS_OF_FROM_PERIOD
    assert out.published_at is None


def test_latest_out_without_row_is_a_skeleton() -> None:
    m = PIG_INDUSTRY.metric("hog_price")
    assert m is not None
    out = service._latest_out(m, None)
    assert out.metric_key == "hog_price"
    assert out.value is None
    assert out.as_of is None
    assert out.as_of_source is None  # 无数据不假装有值：判别式也空
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
    assert body.as_of_source == service.AS_OF_FROM_PUBLISHED  # 判别式随四徽章一起下发
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


@pytest.mark.parametrize("empty", ["metric_keys", "scope_keys"])
async def test_latest_endpoint_rejects_empty_lists_with_422(
    monkeypatch: pytest.MonkeyPatch, empty: str
) -> None:
    """空列表 ≠ 省略：省略=全部，空列表=问了个空问题 → 422（不许静默当成"全部"）。"""
    from app.api.v1 import metrics as api

    async def _never(*_a: Any, **_kw: Any) -> list[Any]:
        raise AssertionError("空列表必须在触 service 前被拒")

    monkeypatch.setattr(service, "get_latest_metrics_across_scopes", _never)
    kwargs: dict[str, Any] = {"metric_keys": None, "scope_keys": None}
    kwargs[empty] = []
    with pytest.raises(HTTPException) as exc:
        await api.get_latest_metrics(db=None, **kwargs)  # type: ignore[arg-type]
    assert exc.value.status_code == 422


@pytest.mark.parametrize("keys", [[""], ["", "  "], ["hog_price", ""]])
async def test_latest_endpoint_normalises_blank_keys(
    monkeypatch: pytest.MonkeyPatch, keys: list[str]
) -> None:
    """`?metric_keys=` 经 HTTP 解析成 `[""]`（不是 `[]`）——空白项必须被剔除。

    剔除后为空 → 422（不许当成"未提供"而返回全部）；剔除后非空 → 用清洗过的列表查。
    """
    from app.api.v1 import metrics as api

    seen: dict[str, Any] = {}

    async def _fake(
        _db: Any, scope_keys: list[str] | None = None, metric_keys: list[str] | None = None
    ) -> list[Any]:
        seen["metric_keys"] = metric_keys
        return []

    monkeypatch.setattr(service, "get_latest_metrics_across_scopes", _fake)
    cleaned = [k for k in keys if k.strip()]
    if cleaned:
        await api.get_latest_metrics(db=None, scope_keys=None, metric_keys=keys)  # type: ignore[arg-type]
        assert seen["metric_keys"] == cleaned
    else:
        with pytest.raises(HTTPException) as exc:
            await api.get_latest_metrics(db=None, scope_keys=None, metric_keys=keys)  # type: ignore[arg-type]
        assert exc.value.status_code == 422


@pytest.mark.parametrize("absurd", ["100000y", "999999999999y"])
async def test_series_endpoint_returns_422_for_absurd_range_never_500(
    monkeypatch: pytest.MonkeyPatch, absurd: str
) -> None:
    """Ruling 36 的靶向回归：超大 range → **422**，且**绝不抛异常**。

    修复前这两个输入在 `SeriesRange.period_from()` 里抛 `OverflowError`
    （`100000y` → days=36_600_000；`999999999999y` → timedelta 自身拒绝），路由的
    except 分支不覆盖它 → HTTP 500。
    """
    from app.api.v1 import metrics as api

    async def _never(*_a: Any, **_kw: Any) -> MetricSeriesOut:
        raise AssertionError("非法 range 必须在触 service 前被拒（否则会 500）")

    monkeypatch.setattr(service, "get_series", _never)
    with pytest.raises(HTTPException) as exc:  # 注意：是 HTTPException，不是 OverflowError
        await api.get_series(
            db=None, cache=_NoneCache(), scope_key="pig", metric_key="hog_price", range=absurd
        )  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "range" in str(exc.value.detail)


async def test_series_endpoint_rejects_bad_range_with_422_not_404() -> None:
    """参数不合法是 422（"资源不存在"才是 404），且在触 DB 前返回。"""
    from app.api.v1 import metrics as api

    with pytest.raises(HTTPException) as exc:
        await api.get_series(
            db=None, cache=_NoneCache(), scope_key="pig", metric_key="hog_price", range="weekly"
        )  # type: ignore[arg-type]
    assert exc.value.status_code == 422
    assert "range" in str(exc.value.detail)


async def test_series_endpoint_caches_with_canonical_range_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缓存键 = 规范化 range 文本：`1y` 与 `366d` 是同一窗口 → 同一把键。

    （原实现用 limit 做键，会把不同窗口的 `30d`/`30m`… 折叠，或反过来让同一窗口
    因字面量不同各存一份。）
    """
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
            limit=(
                service.parse_series_range(range_key) or service.SeriesRange("all", None, 0)
            ).limit,
        )

    monkeypatch.setattr(service, "get_series", _fake)
    cache = _NoneCache()
    for range_key in ("1y", "366d"):
        await api.get_series(
            db=None, cache=cache, scope_key="pig", metric_key="hog_price", range=range_key
        )  # type: ignore[arg-type]
    assert [k for k, _v, _t in cache.sets] == [
        "metrics:series:pig:hog_price:366d",
        "metrics:series:pig:hog_price:366d",
    ]
    # 响应的 range 是规范化文本，请求 `1y` 也不会回一个和窗口不符的字面量
    assert cache.sets[0][1]["range"] == "366d"
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
