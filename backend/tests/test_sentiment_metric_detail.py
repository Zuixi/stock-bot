"""情绪指标详情（`GET /market/sentiment/metrics/{metric_key}`）—— 纯函数 + 端点。

覆盖点（与 `/sentiment/trends` 同一条缺失纪律）：

1. **NULL 不进样本**：`broken_rate` 为 NULL（分母 0）或 `promo_*_n=0` 的日子既不进
   统计量、也不进分位/分布 —— 绝不按 0 计；
2. **样本不足**：有效样本 59 < 60 → `insufficient_history=true` 且
   `percentile_rank=null`（边界 59 vs 60 逐字钉住）；
3. **上一点跨过 NULL**：`delta` 与"较前值"比的是**上一个有效值**，不是行序上一行；
4. **分布守恒**：各箱计数之和 = 有效样本数；样本 < 10 不给分布（不画假直方图）；
5. **连续天数**以窗口中位数为界，遇到 NULL 或恰好等于中位数即停；
6. **切换器**（`peers`）覆盖 9 个指标且与 schema 常量同序；
7. 未知 `metric_key` → **404**（不是空图）。

**离线**：默认门禁用例全部打桩，不碰 PG / Redis / 网络。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from httpx import ASGITransport

from app.api.deps import get_cache
from app.main import app
from app.repositories import limit_up_repo
from app.schemas.limit_up import MIN_PERCENTILE_SAMPLES, SENTIMENT_METRIC_KEYS
from app.services import sentiment_metric_service as sms

D_START = date(2025, 10, 15)


def _days(n: int, *, offset: int = 0) -> list[date]:
    return [D_START + timedelta(days=offset + i) for i in range(n)]


def _row(
    day: date,
    *,
    zt: int = 40,
    dt: int = 5,
    zb: int = 20,
    broken_rate: float | None = 0.33,
    yzt_avg_pct: float | None = 1.0,
    yzt_avg_open_premium: float | None = 0.5,
    yzt_n: int = 10,
    promo_1to2: float | None = 0.25,
    promo_1to2_n: int = 10,
    promo_2to3: float | None = 0.5,
    promo_2to3_n: int = 10,
    max_streak: int = 5,
) -> SimpleNamespace:
    """一行 ``market_sentiment_daily``（ORM 行替身：直读属性）。"""
    return SimpleNamespace(
        trade_date=day,
        zt_count=zt,
        dt_count=dt,
        zb_count=zb,
        broken_rate=broken_rate,
        yzt_avg_pct=yzt_avg_pct,
        yzt_avg_open_premium=yzt_avg_open_premium,
        yzt_n=yzt_n,
        promo_1to2=promo_1to2,
        promo_1to2_n=promo_1to2_n,
        promo_2to3=promo_2to3,
        promo_2to3_n=promo_2to3_n,
        max_streak=max_streak,
        max_streak_symbol="600000",
        source="local_calc",
    )


def _payload(rows: list[Any], key: str, *, days: int | None = None) -> dict[str, Any]:
    return sms.metric_payload(
        rows,
        key,
        as_of=rows[-1].trade_date if rows else None,
        as_of_quality="complete",
        days=days if days is not None else len(rows),
    )


class _RecordingCache:
    def __init__(self) -> None:
        self.store: dict[str, Any] = {}
        self.gets: list[str] = []
        self.sets: list[tuple[str, Any, int | None]] = []

    async def get(self, key: str) -> Any:
        self.gets.append(key)
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.sets.append((key, value, ttl))
        self.store[key] = value


# ---------------------------------------------------------------------------
# 1. 有效样本：NULL / 分母 0 都不进样本
# ---------------------------------------------------------------------------


def test_null_and_zero_denominator_days_are_excluded() -> None:
    """`broken_rate` NULL 日与 `promo_1to2_n=0` 日都不进有效样本。"""
    days = _days(10)
    rows = [_row(d, broken_rate=None, promo_1to2=0.25, promo_1to2_n=0) for d in days[:5]]
    rows += [_row(d, broken_rate=0.5, promo_1to2=0.5, promo_1to2_n=8) for d in days[5:]]

    broken = _payload(rows, "broken_rate")
    assert broken["stats"]["sample_count"] == 5  # 5 个 NULL 不进样本
    assert broken["series"][0]["value"] is None
    # 炸板率的样本数 = 涨停 + 炸板（这一天 broken_rate 为 NULL，但分母仍是 60）
    assert broken["series"][0]["n"] == 60

    promo = _payload(rows, "promo_1to2")
    assert promo["stats"]["sample_count"] == 5
    # n=0 的原始值仍展示（那一天确实算出了 25%），但标记 low_sample、不进统计
    assert promo["series"][0]["value"] == 0.25
    assert promo["series"][0]["low_sample"] is True
    assert promo["series"][0]["n"] == 0


def test_count_metric_n_is_null_not_zero() -> None:
    """非样本型指标（涨停家数）的 `n` 恒 `null`：0 会被读成"样本 0 家"。"""
    rows = [_row(d) for d in _days(5)]
    body = _payload(rows, "zt_count")
    assert all(point["n"] is None for point in body["series"])


# ---------------------------------------------------------------------------
# 2. 统计量 / 分位 / 分布
# ---------------------------------------------------------------------------


def test_display_days_slices_series_but_keeps_regime_context() -> None:
    """`display_days=20` 只切展示/统计；阶段仍用 250 日上下文（不能从窗口起点重算）。"""
    rows = [_row(d, zt=20 + i, broken_rate=0.6, max_streak=3) for i, d in enumerate(_days(60))]
    payload = sms.metric_payload(
        rows,
        "zt_count",
        as_of=rows[-1].trade_date,
        as_of_quality="complete",
        days=20,
        display_days=20,
    )
    assert payload["window"]["samples"] == 20
    assert len(payload["series"]) == 20
    assert payload["regime"]["segments"][0]["from"] == rows[0].trade_date


def test_percentile_rank_mean_and_median() -> None:
    """zt 从 1 到 60：最新值 60 的分位 = 100，中位数 30.5，均值 30.5。"""
    rows = [_row(d, zt=i + 1) for i, d in enumerate(_days(60))]
    body = _payload(rows, "zt_count")
    assert body["stats"]["sample_count"] == 60
    assert body["stats"]["median"] == 30.5
    assert body["stats"]["mean"] == 30.5
    assert body["stats"]["min"] == 1
    assert body["stats"]["max"] == 60
    assert body["percentile_rank"] == 100.0
    assert body["insufficient_history"] is False


def test_percentile_rank_uses_less_or_equal() -> None:
    """`<=` 口径：一半样本等于最新值 → 50 分位（不是 49.x）。"""
    rows = [_row(d, zt=10) for d in _days(30)] + [_row(d, zt=20) for d in _days(30, offset=30)]
    body = _payload(rows, "zt_count")
    assert body["percentile_rank"] == 100.0  # 60 个样本里 <=20 的有 60 个

    rows2 = [_row(d, zt=20) for d in _days(30)] + [_row(d, zt=10) for d in _days(30, offset=30)]
    body2 = _payload(rows2, "zt_count")
    assert body2["percentile_rank"] == 50.0


def test_insufficient_history_boundary_59_vs_60() -> None:
    """59 → 不足且分位 None；60 → 恰好够。"""
    short = _payload([_row(d, zt=i + 1) for i, d in enumerate(_days(59))], "zt_count")
    assert short["insufficient_history"] is True
    assert short["percentile_rank"] is None

    exact = _payload([_row(d, zt=i + 1) for i, d in enumerate(_days(60))], "zt_count")
    assert exact["insufficient_history"] is False
    assert exact["percentile_rank"] == 100.0
    assert MIN_PERCENTILE_SAMPLES == 60


def test_distribution_bins_sum_to_samples_and_cover_range() -> None:
    """10 箱等宽：[min,max] 覆盖，计数之和 = 有效样本数。"""
    rows = [_row(d, zt=i + 1) for i, d in enumerate(_days(60))]
    body = _payload(rows, "zt_count")
    bins = body["distribution"]
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == 60
    assert bins[0]["from"] == 1
    assert bins[-1]["to"] == 60
    # 箱边界单调且相邻
    for left, right in zip(bins, bins[1:]):
        assert left["to"] == right["from"]


def test_distribution_empty_when_too_few_samples() -> None:
    """有效样本 < 10 → 不给分布（避免 3 个点画出"看起来像分布"的图）。"""
    rows = [_row(d) for d in _days(9)]
    assert _payload(rows, "zt_count")["distribution"] == []


def test_distribution_single_bin_when_all_equal() -> None:
    """全部同值 → 单箱（不是 10 个 0 宽度的箱）。"""
    rows = [_row(d, zt=7) for d in _days(12)]
    bins = _payload(rows, "zt_count")["distribution"]
    assert len(bins) == 1
    assert bins[0]["from"] == bins[0]["to"] == 7
    assert bins[0]["count"] == 12


# ---------------------------------------------------------------------------
# 3. 较前值 / 连续天数
# ---------------------------------------------------------------------------


def test_previous_skips_null_days() -> None:
    """最新日有效、它前面一天 NULL → `previous` 取再往前一个**有效**日。"""
    rows = [
        _row(_days(3)[0], broken_rate=0.2),
        _row(_days(3)[1], broken_rate=None),
        _row(_days(3)[2], broken_rate=0.5),
    ]
    body = _payload(rows, "broken_rate")
    assert body["latest"]["value"] == 0.5
    assert body["previous"]["trade_date"] == _days(3)[0]
    assert body["delta"] == pytest.approx(0.3)


def test_latest_null_means_no_delta() -> None:
    rows = [
        _row(_days(2)[0], broken_rate=0.2),
        _row(_days(2)[1], broken_rate=None),
    ]
    body = _payload(rows, "broken_rate")
    assert body["latest"]["value"] is None
    assert body["delta"] is None
    assert body["percentile_rank"] is None


def test_streak_breaks_on_median_and_null() -> None:
    """连续天数：以中位数为界，遇到 NULL 或恰好等于中位数即停。"""
    # 5 天：1, 3, 5, 5(NULL), 9；中位数 3（有效样本 1,3,5,9 → 4）→ 不太可控，
    # 改用明确序列：10,20,30,40,50，中位数 30，最新 50 之上连续 2 天（50,40），30 处停。
    rows = [_row(d, zt=v) for d, v in zip(_days(5), [10, 20, 30, 40, 50])]
    body = _payload(rows, "zt_count")
    assert body["streak"]["median"] == 30
    assert body["streak"]["side"] == "above"
    assert body["streak"]["days"] == 2

    # NULL 截断：10, NULL, 20 → 中位数 15，最新 20 在上方，连续 1 天（NULL 断开）
    rows_null = [
        _row(_days(3)[0], zt=10),
        _row(_days(3)[1], zt=None),
        _row(_days(3)[2], zt=20),
    ]
    body_null = _payload(rows_null, "zt_count")
    assert body_null["streak"]["side"] == "above"
    assert body_null["streak"]["days"] == 1


# ---------------------------------------------------------------------------
# 4. 切换器 / 注册表 / 未知指标
# ---------------------------------------------------------------------------


def test_peers_cover_registry_in_order() -> None:
    rows = [_row(d) for d in _days(60)]
    body = _payload(rows, "zt_count")
    assert [p["key"] for p in body["peers"]] == list(SENTIMENT_METRIC_KEYS)
    assert all(p["latest_value"] is not None for p in body["peers"])


def test_registry_matches_schema_constant() -> None:
    assert tuple(sms.METRIC_DEFS) == SENTIMENT_METRIC_KEYS


def test_unknown_metric_raises_key_error() -> None:
    with pytest.raises(KeyError):
        _payload([_row(_days(1)[0])], "not_a_metric")


# ---------------------------------------------------------------------------
# 5. 端点级：200 形状 + 404
# ---------------------------------------------------------------------------


@pytest.fixture
async def _client() -> Any:
    cache = _RecordingCache()

    async def _cache_dep() -> Any:
        return cache

    app.dependency_overrides[get_cache] = _cache_dep
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, cache
    finally:
        app.dependency_overrides.clear()


class _FakeSessionFactory:
    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __call__(self) -> _FakeSessionFactory:
        return self


def _patch_db(monkeypatch: pytest.MonkeyPatch, rows: list[Any]) -> None:
    from app.services import market_day_service

    async def _fake_get(db: Any) -> date | None:
        return rows[-1].trade_date if rows else None

    async def _fake_list(db: Any, days: int) -> list[Any]:
        return rows[-days:] if days > 0 else []

    async def _fake_quality(db: Any, *, cache: Any = None) -> Any:
        return SimpleNamespace(day=rows[-1].trade_date, quality="complete")

    monkeypatch.setattr(sms, "async_session_factory", _FakeSessionFactory(object()))
    monkeypatch.setattr(limit_up_repo, "latest_sentiment_day", _fake_get)
    monkeypatch.setattr(limit_up_repo, "list_sentiment_days", _fake_list)
    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _fake_quality)


async def test_metric_endpoint_shape(monkeypatch: pytest.MonkeyPatch, _client: Any) -> None:
    client, cache = _client
    rows = [_row(d, zt=i + 1) for i, d in enumerate(_days(60))]
    _patch_db(monkeypatch, rows)

    resp = await client.get("/api/v1/market/sentiment/metrics/zt_count", params={"days": 60})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["metric"]["key"] == "zt_count"
    assert body["metric"]["label"] == "涨停家数"
    assert body["as_of"] == rows[-1].trade_date.isoformat()
    assert body["window"] == {
        "days": 60,
        "from": rows[0].trade_date.isoformat(),
        "to": rows[-1].trade_date.isoformat(),
        "samples": 60,
    }
    assert body["latest"]["value"] == 60
    assert body["percentile_rank"] == 100.0
    # 直方图的区间键必须是 `from`（Pydantic alias），前端按 `bin.from` 读取
    assert "from" in body["distribution"][0]
    assert sum(b["count"] for b in body["distribution"]) == 60
    assert set(body["series"][0]) == {"trade_date", "value", "n", "low_sample"}
    assert any(":60" in key for key in cache.gets), cache.gets

    # 越界参数 → 422
    over = await client.get("/api/v1/market/sentiment/metrics/zt_count", params={"days": 501})
    assert over.status_code == 422


async def test_metric_endpoint_unknown_key_404(_client: Any) -> None:
    client, _ = _client
    resp = await client.get("/api/v1/market/sentiment/metrics/not_a_metric")
    assert resp.status_code == 404, resp.text
    assert "unknown sentiment metric" in resp.json()["detail"]
