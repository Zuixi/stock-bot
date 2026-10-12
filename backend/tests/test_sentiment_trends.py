"""Task 6a：`GET /market/sentiment/trends`（读时计算，不物化）+ 分位纯函数。

覆盖点（按 brief 与 controller rulings）：

1. **分位只用有效样本**：某字段当日为 NULL（如 `promo_1to2` 的分母为 0）→ 该日**不进**
   该字段的样本、也**绝不**计 0。样本数少算一天会静默移动分位（Ruling 4）。
2. **`insufficient_history` 边界**：`samples < 60` → `insufficient_history=true` 且
   `percentile=null`（59 vs 60 逐字钉住，端点值 0/100 都要可达且正确）。
3. **读窗口守恒**：`days` 缺省 250 / 上限 500；`series` 升序；`window.from`/`to` =
   首/末纳入日；`samples` = 窗口内 `market_sentiment_daily` 行数。
4. **缓存键含全部维度**（`days`）；**空结果不缓存**（空图不得被冻结）。
5. **`streak.zt_above_median_days`**：窗口内 `zt_count > 窗口中位数` 的天数。

**离线**：默认门禁用例全部打桩（repo 替身 / 假 session），不碰 PG、Redis、网络。
真库用例标 ``e2e``（默认 deselect），只读 `market_sentiment_daily`（T4 回补后的 239 天）。
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
from app.services import limit_up_service as lus

# 2025-10-15 是 T4 回补后的第一个交易日（安全起点）；用它做桩窗口的锚
D_START = date(2025, 10, 15)


def _days(n: int) -> list[date]:
    """n 个"交易日"（自然日连续即可 —— 本文件的桩不校验日历真实性）。"""
    return [D_START + timedelta(days=i) for i in range(n)]


def _row(
    day: date,
    *,
    zt: int = 40,
    dt: int = 5,
    zb: int = 20,
    broken_rate: float | None = 0.33,
    promo_1to2: float | None = 0.25,
    promo_1to2_n: int = 10,
    max_streak: int = 5,
) -> SimpleNamespace:
    """一行 ``market_sentiment_daily``（ORM 行替身：直读属性）。"""
    return SimpleNamespace(
        trade_date=day,
        zt_count=zt,
        dt_count=dt,
        zb_count=zb,
        broken_rate=broken_rate,
        yzt_avg_pct=1.0,
        promo_1to2=promo_1to2,
        promo_1to2_n=promo_1to2_n,
        promo_2to3=None,
        promo_2to3_n=0,
        max_streak=max_streak,
        max_streak_symbol="600000",
        source="backfill",
    )


class _ScalarsResult:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = list(rows)

    def scalars(self) -> _ScalarsResult:
        return self

    def all(self) -> list[Any]:
        return list(self._rows)

    def scalar_one_or_none(self) -> Any:
        return self._rows[0] if self._rows else None


class _FakeSessionFactory:
    """`async_session_factory()` 的替身上下文（默认门禁绝不触 PG）。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: Any) -> None:
        return None

    def __call__(self) -> _FakeSessionFactory:
        return self


class _FakeDb:
    """按语句里的表名/日期分支的最小 session 替身（不连库）。"""

    def __init__(
        self,
        *,
        days: list[date] | None = None,
        rows: list[SimpleNamespace] | None = None,
        market_days: list[date] | None = None,
    ) -> None:
        self.days = list(days or [])
        self.rows = list(rows or [])
        self.market_days = list(market_days or [])

    async def execute(self, stmt: Any, params: Any = None) -> _ScalarsResult:
        return _ScalarsResult(self.rows)


class _RecordingCache:
    """只记录 get/set 的缓存替身。"""

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
# 1. 分位纯函数：有效样本 / 样本不足 / 0-100 边界
# ---------------------------------------------------------------------------


def test_percentile_excludes_null_days_from_sample_count() -> None:
    """NULL 日**不进**样本，也**绝不**计 0。

    构造 60 天：前 59 天 `broken_rate=0.33`，最后一天 NULL。
    - "NULL 当 0"会算出 `p0=0.0`（min 被 NULL 的假 0 拉低）→ 本断言会红；
    - 正确口径：`sample_count=59`、`p0=p100=0.33`。
    同一天 `zt_count` 有效 → 该字段 `sample_count=60`（两字段样本**各自**计数）。
    """
    rows = [_row(d, zt=40, broken_rate=0.33 if i < 59 else None) for i, d in enumerate(_days(60))]

    series = [lus.sentiment_series_row(r) for r in rows]
    p = lus.percentile_from_series(series, "broken_rate")

    assert p is not None
    assert p["sample_count"] == 59
    assert p["p0"] == 0.33
    assert p["p50"] == 0.33
    assert p["p100"] == 0.33

    q = lus.percentile_from_series(series, "zt_count")
    assert q is not None
    assert q["sample_count"] == 60

    # 全日 NULL → 该字段样本 0 → 分位整体 None（不是 0）
    all_null = [lus.sentiment_series_row(_row(d, broken_rate=None)) for d in _days(3)]
    assert lus.percentile_from_series(all_null, "broken_rate") is None


def test_percentile_null_day_does_not_shift_sample_count() -> None:
    """60 个有效样本 + 1 个 NULL 值日 → 有效样本 60，且中位数仍是被删掉的那段。

    若把 NULL 当 0，`p50` 会掉到 0.0（0 与 0.9 的分布中位）—— 故 p50 就是"NULL 未计 0"
    的判据，而非只看 `sample_count`。
    """
    days = _days(60)
    rows = [_row(d, promo_1to2=0.9) for d in days]
    rows.append(_row(days[-1] + timedelta(days=1), promo_1to2=None, promo_1to2_n=0))

    series = [lus.sentiment_series_row(r) for r in rows]
    p = lus.percentile_from_series(series, "promo_1to2")

    assert p is not None
    assert p["sample_count"] == 60
    assert p["p0"] == 0.9
    assert p["p50"] == 0.9
    assert p["p100"] == 0.9


def test_percentile_endpoints_reachable() -> None:
    """0 / 100 端点可达且正确：p0 = min，p100 = max（nearest-rank 上取整）。

    用 1..60 做样本，手算验证 nearest-rank 定义（rank = ceil(pct/100 × n)）：
    p0 → 第 1 个（值 1）、p50 → 第 30 个（值 30）、p100 → 第 60 个（值 60）。
    """
    rows = [_row(d, zt=i + 1) for i, d in enumerate(_days(60))]
    series = [lus.sentiment_series_row(r) for r in rows]

    p = lus.percentile_from_series(series, "zt_count")
    assert p is not None
    assert (p["p0"], p["p50"], p["p100"]) == (1, 30, 60)
    assert p["sample_count"] == 60


def test_insufficient_history_boundary_59_vs_60() -> None:
    """59 个样本 → 不足；60 个样本 → 恰好够（边界逐字钉住）。"""
    short = lus.sentiment_trends_payload(
        [_row(d) for d in _days(59)],
        as_of=_days(59)[-1],
        as_of_quality="complete",
        days=59,
    )
    assert short["insufficient_history"] is True
    assert short["percentile"] is None
    assert short["window"]["samples"] == 59

    exact = lus.sentiment_trends_payload(
        [_row(d) for d in _days(60)],
        as_of=_days(60)[-1],
        as_of_quality="complete",
        days=60,
    )
    assert exact["insufficient_history"] is False
    assert exact["percentile"] is not None
    assert exact["window"]["samples"] == 60
    assert set(exact["percentile"]) == {"zt_count", "broken_rate", "promo_1to2"}


def test_zt_above_median_days_and_series_order() -> None:
    """`series` 升序；`zt_above_median_days` = 严格大于中位数的天数。"""
    zts = [10, 20, 30, 40, 50]
    rows = [_row(d, zt=z) for d, z in zip(_days(5), zts)]
    payload = lus.sentiment_trends_payload(
        rows, as_of=rows[-1].trade_date, as_of_quality="complete", days=5
    )

    assert [s["trade_date"] for s in payload["series"]] == _days(5)
    # 中位数 = 30 → 40/50 两天大于它
    assert payload["streak"]["zt_above_median_days"] == 2
    assert payload["window"]["from"] == _days(5)[0]
    assert payload["window"]["to"] == _days(5)[-1]


async def test_empty_result_is_never_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """空结果不得进缓存（空图不能被冻结成 900s 的"暂无数据"）。"""
    from app.services import market_day_service

    async def _no_day(db: Any) -> Any:
        return None

    async def _no_rows(db: Any, days: int) -> list[Any]:
        return []

    async def _no_as_of(db: Any) -> None:
        return None

    monkeypatch.setattr(lus, "async_session_factory", _FakeSessionFactory(object()))
    monkeypatch.setattr(limit_up_repo, "latest_sentiment_day", _no_as_of)
    monkeypatch.setattr(limit_up_repo, "list_sentiment_days", _no_rows)
    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _no_day)

    cache = _RecordingCache()
    payload = await lus.get_sentiment_trends(cache, days=250)

    assert payload["series"] == []
    assert payload["window"]["samples"] == 0
    assert payload["insufficient_history"] is True
    assert payload["percentile"] is None
    assert cache.sets == []
    assert cache.gets == ["market:limit-up:trends:250"]


# ---------------------------------------------------------------------------
# 2. repo 窗口查询口径（编译 SQL / 参数断言）
# ---------------------------------------------------------------------------


async def test_list_sentiment_days_window_is_bounded_and_ascending() -> None:
    """窗口读：降序取 N 行后**升序**返回（跨月时序图从左到右）。"""
    rows = [_row(d) for d in _days(3)]
    db = _FakeDb(rows=list(reversed(rows)))
    out = await limit_up_repo.list_sentiment_days(db, 3)  # type: ignore[arg-type]
    assert [r.trade_date for r in out] == [d for d in _days(3)]


async def test_latest_sentiment_day_reads_max_trade_date() -> None:
    """`as_of` 取表内 `max(trade_date)`（不是"今天"）。"""
    db = _FakeDb(rows=[_days(4)[-1]])
    assert await limit_up_repo.latest_sentiment_day(db) == _days(4)[-1]  # type: ignore[arg-type]


async def test_sentiment_days_in_range_filters_and_sorts() -> None:
    """给定区间内的交易日（升序）—— 供 `missing_days` / 日历对拍。"""
    db = _FakeDb(rows=[_days(6)[i] for i in (1, 2, 3, 4)])
    out = await limit_up_repo.sentiment_days_in_range(db, _days(6)[1], _days(6)[4])  # type: ignore[arg-type]
    assert out == _days(6)[1:5]


# ---------------------------------------------------------------------------
# 3. 端点级：200 + 响应形状（含缓存键）
# ---------------------------------------------------------------------------


@pytest.fixture
async def _api_cache() -> Any:

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


def _patch_sentiment_db(
    monkeypatch: pytest.MonkeyPatch,
    *,
    rows: list[Any],
    quality: Any,
) -> None:
    """把 6a 的读路径整体换成桩。

    **必须连 `async_session_factory` 一起换**：只要还有一个调用点走真 session，
    这个用例就会在 PG 上开一条绑在"本用例事件循环"的连接，用例结束后循环关闭 →
    下个 async 用例拿到 `Event loop is closed`（全量 suite 实测踩过）。
    """

    async def _fake_get(db: Any) -> date | None:
        return rows[-1].trade_date if rows else None

    async def _fake_list(db: Any, days: int) -> list[Any]:
        return rows[-days:] if days > 0 else []

    async def _fake_quality(db: Any, *, cache: Any = None) -> Any:
        return quality

    async def _fake_recent(db: Any, as_of: date, limit: int) -> list[date]:
        return [r.trade_date for r in rows[-limit:]]

    from app.services import market_day_service

    monkeypatch.setattr(lus, "async_session_factory", _FakeSessionFactory(object()))
    monkeypatch.setattr(limit_up_repo, "latest_sentiment_day", _fake_get)
    monkeypatch.setattr(limit_up_repo, "list_sentiment_days", _fake_list)
    monkeypatch.setattr(limit_up_repo, "list_recent_trade_dates", _fake_recent)
    monkeypatch.setattr(market_day_service, "resolve_latest_complete_day", _fake_quality)


async def test_sentiment_trends_endpoint_shape_and_days_bounds(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any
) -> None:
    """端点 200；`days` 上下界（>500 → 422）；缓存键含 `days`。"""
    client, cache = _api_cache
    rows = [_row(d) for d in _days(60)]
    _patch_sentiment_db(
        monkeypatch,
        rows=rows,
        quality=SimpleNamespace(day=_days(60)[-1], quality="complete"),
    )

    resp = await client.get("/api/v1/market/sentiment/trends", params={"days": 60})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == _days(60)[-1].isoformat()
    assert body["as_of_quality"] == "complete"
    assert body["window"]["days"] == 60
    assert body["window"]["samples"] == 60
    assert body["window"]["from"] == _days(60)[0].isoformat()
    assert body["window"]["to"] == _days(60)[-1].isoformat()
    assert body["insufficient_history"] is False
    assert set(body["percentile"]) == {"zt_count", "broken_rate", "promo_1to2"}
    assert body["series"][0]["trade_date"] == _days(60)[0].isoformat()
    assert body["series"][0].keys() >= {
        "trade_date",
        "zt_count",
        "dt_count",
        "zb_count",
        "broken_rate",
        "promo_1to2",
        "max_streak",
    }
    assert any(":60" in key for key in cache.gets), cache.gets

    # 越界：501 > 500 → 422（FastAPI Query le=500）
    resp_over = await client.get("/api/v1/market/sentiment/trends", params={"days": 501})
    assert resp_over.status_code == 422, resp_over.text
    resp_low = await client.get("/api/v1/market/sentiment/trends", params={"days": 1})
    assert resp_low.status_code == 422, resp_low.text


async def test_sentiment_trends_days_30_insufficient(
    monkeypatch: pytest.MonkeyPatch, _api_cache: Any
) -> None:
    """`days=30` → `insufficient_history=true` 且 `percentile=null`（真库验收同款口径）。"""
    client, _ = _api_cache
    _patch_sentiment_db(
        monkeypatch,
        rows=[_row(d) for d in _days(30)],
        quality=SimpleNamespace(day=_days(30)[-1], quality="complete"),
    )

    resp = await client.get("/api/v1/market/sentiment/trends", params={"days": 30})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["insufficient_history"] is True
    assert body["percentile"] is None
    assert body["window"]["samples"] == 30


# ---------------------------------------------------------------------------
# 4. 真库（`-m e2e`）：T4 回补后的 239 天窗口
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_sentiment_trends_real_db_250_days() -> None:
    """真库验收：`days=250` → `samples ≥ 230`（依赖 T4 回补）；`days=30` → 样本不足。"""
    from app.core.database import engine

    cache = _RecordingCache()
    try:
        payload = await lus.get_sentiment_trends(cache, days=250)
    finally:
        await engine.dispose()

    assert payload["window"]["samples"] >= 230, payload["window"]
    assert payload["insufficient_history"] is False
    assert payload["percentile"] is not None
    assert payload["series"] == sorted(payload["series"], key=lambda r: r["trade_date"])
    assert payload["window"]["from"] == payload["series"][0]["trade_date"]
    assert payload["window"]["to"] == payload["series"][-1]["trade_date"]

    short = await lus.get_sentiment_trends(cache, days=30)
    assert short["insufficient_history"] is True
    assert short["percentile"] is None
