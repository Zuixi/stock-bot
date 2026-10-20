"""Task 21：`GET /api/v1/market/sentiment/composite`（S1.13 hero，读时计算，不物化）。

覆盖点：

1. **分位定义**：`100 × count(v <= 今天) / n`（nearest-rank，无插值，四舍五入 2 位），
   只数**有效样本**（NULL 行既不进样本也不计 0）；窗口是**尾部** `window` 个样本。
2. **`insufficient_history`**：`< MIN_PERCENTILE_SAMPLES`(60) → `percentile=null`
   且 `insufficient_history=true`（59 vs 60 逐字钉住，不给假分位 —— Ruling 4）。
3. **直读 `extra`、缺键不发明**：`legs`/`effective_weight`/`min_effective_weight`/
   `total_legs` 取自最新一行的既有键；老行没有这些键 → `null`/0。
4. **端点级**：匿名（无 cookie/token）200 + 响应键逐字等于契约；`window` 恒 1260。
5. **今天的值为 NULL** → `value=null` 且 `percentile=null`（没有"今天的位置"）。

**离线**：默认门禁用例全部打桩（repo 替身 + 假 session），不碰 PG、Redis、网络。
真库用例标 ``e2e``（默认 deselect）。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport

from app.main import app
from app.repositories import industry_metric_repo
from app.schemas.limit_up import MIN_PERCENTILE_SAMPLES
from app.services import market_day_service as mds
from app.services import market_sentiment_composite_service as mscs


@pytest.fixture(autouse=True)
def _disable_composite_hero_cache():
    """CI 有真 Redis、本机默认没有：hero 的 120s 整包缓存会跨用例串数据.

    这些用例全部打桩读路径、**断言本次打桩的结果**；缓存命中会直接返回上一条用例的
    payload（CI 实测：第二个用例拿到第一个的 `expected_as_of`，`stale` 从 None 变 False）。
    把 `get_cache` 依赖覆盖成 None，既隔离用例，也避免真 Redis 客户端跨事件循环复用
    （CI 的 `RuntimeError: Event loop is closed` 同一来源）。
    """
    from app.api.deps import get_cache

    app.dependency_overrides[get_cache] = lambda: None
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_cache, None)


# 契约冻结的窗口（交易日）
CONTRACT_WINDOW = 1260
CONTRACT_KEYS = {
    "as_of",
    "value",
    "window",
    "sample_count",
    "percentile",
    "insufficient_history",
    "legs",
    "leg_count",
    "total_legs",
    "effective_weight",
    "min_effective_weight",
    "caliber_version",
    "expected_as_of",
    "lag_trading_days",
    "stale",
    "provisional",
    # 2026-10-19：被发布闸门拦下的当日行（解释"为什么 hero 不是它"）
    "latest_withheld",
}

#: 11 条腿的 key（`_healthy_row` 的默认参与集，形状与齐备日一致）。
ALL_LEGS = [
    "qvix_50etf",
    "if_basis",
    "ic_basis",
    "im_basis",
    "idx_turnover_ratio_csi300",
    "margin_financing_balance",
    "margin_financing_buy_ratio",
    "above_ma20_ratio",
    "advance_decline_ratio",
    "limit_up_break_ratio",
    "max_consecutive_limit_up",
]

D_START = date(2025, 10, 15)

#: 2026-10-19 当晚实际缺席的三条腿（两融 T+1 ×2 + QVIX 源滞后）—— 与生产日志逐字对齐。
_MISSING_1019 = {
    "qvix_50etf",
    "margin_financing_balance",
    "margin_financing_buy_ratio",
}


def _days(n: int) -> list[date]:
    return [D_START + timedelta(days=i) for i in range(n)]


def _row(
    day: date,
    value: float | Decimal | None,
    *,
    participants: list[str] | None | object = ...,
    n_constituents: int | object = ...,
    effective_weight_sum: float | object = ...,
    min_effective_weight: float | object = ...,
    caliber: str | None = "s1.13-v1",
) -> SimpleNamespace:
    """一行 `industry_metrics`（行替身：直读 `period`/`value`/`extra`/`caliber_version`）。

    `...`（默认）= 该键**不写入** `extra`（模拟老行缺键）；显式 `None` = 键存在但值为 null。
    """
    extra: dict[str, Any] = {}
    if participants is not ...:
        extra["participants"] = participants
    if n_constituents is not ...:
        extra["n_constituents"] = n_constituents
    if effective_weight_sum is not ...:
        extra["effective_weight_sum"] = effective_weight_sum
    if min_effective_weight is not ...:
        extra["min_effective_weight"] = min_effective_weight
    return SimpleNamespace(
        period=day,
        value=value,
        extra=extra or None,
        caliber_version=caliber,
    )


def _full_row(day: date, value: float | Decimal | None = 32.8157) -> SimpleNamespace:
    """**齐备日**的行（11 条腿 / 生效权重 1.0，`extra` 四键齐全）。

    这是发布闸门之上的正常形状 —— `_full_row` 是"过闸门"的基准行。
    """
    return _row(
        day,
        value,
        participants=list(ALL_LEGS),
        n_constituents=11,
        effective_weight_sum=1.0,
        min_effective_weight=0.2,
    )


def _thin_row(day: date, value: float | Decimal | None = 32.8157) -> SimpleNamespace:
    """**薄行**：与库内 2026-10-12 同形状（3 条基差腿 / 生效权重 0.23）。

    它过了 `compose_series` 的方法下限（0.20）但**过不了发布闸门**（0.80）——
    读面不得把它当头条。
    """
    return _row(
        day,
        value,
        participants=["if_basis", "ic_basis", "im_basis"],
        n_constituents=11,
        effective_weight_sum=0.23,
        min_effective_weight=0.2,
    )


# ---------------------------------------------------------------------------
# 1. 分位纯函数：定义 / 尾窗 / 样本不足边界
# ---------------------------------------------------------------------------


def test_composite_percentile_count_le_definition() -> None:
    """`100 × count(v <= 末值) / n`；末值自身计入 tie；**不插值**。"""
    # min_samples 显式放到 1：本用例只验定义，样本充足性另有边界用例
    n, pct = mscs.composite_percentile([5.0, 1.0, 3.0, 2.0, 4.0], 5, min_samples=1)
    assert (n, pct) == (5, 80.0)  # 4 个 <= 4（4 自身计入）

    # 最小值 → 100/n（不是 0：nearest-rank 的端点可达）
    assert mscs.composite_percentile([10.0, 10.0, 5.0], 3, min_samples=1) == (
        3,
        round(100 / 3, 2),
    )
    # 全部相等 → 100（tie 一起计入，与 indicators.percentile_rank 同口径）
    assert mscs.composite_percentile([9.0, 9.0, 9.0], 3, min_samples=1) == (3, 100.0)


def test_composite_percentile_window_is_tail() -> None:
    """窗口只取**尾部** `window` 个：裁掉窗口前的历史会改变答案。"""
    values = [float(v) for v in range(1, 101)] + [1.0]  # 1..100 再回到 1
    # 尾窗内只有 1 个 <= 1
    assert mscs.composite_percentile(values, 100, min_samples=1) == (100, 1.0)
    # 全序列有 2 个 <= 1
    assert mscs.composite_percentile(values, 200, min_samples=1) == (
        101,
        round(200 / 101, 2),
    )

    # window <= 0 不炸（防御）：空样本 → None
    assert mscs.composite_percentile(values, 0) == (0, None)


def test_composite_percentile_insufficient_history_boundary() -> None:
    """59 个样本 → 不足；60 → 恰好够（边界逐字钉住）。"""
    assert MIN_PERCENTILE_SAMPLES == 60
    assert mscs.composite_percentile([float(i) for i in range(59)], 1260) == (59, None)
    n, pct = mscs.composite_percentile([float(i) for i in range(60)], 1260)
    assert n == 60
    assert pct is not None

    # 空序列同样不给分位
    assert mscs.composite_percentile([], 1260) == (0, None)


def test_composite_percentile_min_samples_is_overridable() -> None:
    """`min_samples` 是参数（默认 Ruling 4 的 60）：传 3 时 3 个样本够。"""
    n, pct = mscs.composite_percentile([1.0, 2.0, 3.0], 1260, min_samples=3)
    assert n == 3
    assert pct == 100.0


# ---------------------------------------------------------------------------
# 2. payload 纯函数：契约字段 / 直读 extra / 缺键不发明 / NULL 今天
# ---------------------------------------------------------------------------


def test_composite_hero_payload_reads_latest_row_extra() -> None:
    """齐备日作头条：11 条腿 / 生效权重 1.0，`extra` 直读（不发明、不现算）。"""
    rows = [_row(d, 20.0 + i) for i, d in enumerate(_days(59))]  # 20..78
    rows.append(_full_row(date(2026, 10, 12), Decimal("32.8157")))

    payload = mscs.composite_hero_payload(rows)

    assert set(payload) == CONTRACT_KEYS
    assert payload["as_of"] == date(2026, 10, 12)
    assert payload["value"] == 32.8157  # Numeric(18,4) → Decimal → float
    assert payload["window"] == CONTRACT_WINDOW
    assert payload["sample_count"] == 60
    # 20..32 共 13 个 <= 32.8157，加末值自身 = 14
    assert payload["percentile"] == round(100 * 14 / 60, 2)
    assert payload["insufficient_history"] is False
    assert payload["legs"] == ALL_LEGS
    assert payload["leg_count"] == 11
    assert payload["total_legs"] == 11
    assert payload["effective_weight"] == 1.0
    assert payload["min_effective_weight"] == 0.2
    assert payload["caliber_version"] == "s1.13-v1"


def test_composite_hero_payload_skips_thin_latest_row() -> None:
    """**薄行不作头条**（2026-10-16 口径）：12 日 3 条腿/0.23 ⇒ 退回 09 日齐备行.

    这是用户报的"数字对不上"的直接回归：3 条腿的值（0.23）与 10 条腿的值（0.88）
    不可比，故读面必须跳过它，并把 `as_of` 落在上一个过闸门的行上。
    """
    older = _row(
        date(2026, 10, 9),
        44.5068,
        participants=[f"leg_{i}" for i in range(10)],
        n_constituents=11,
        effective_weight_sum=0.88,
        min_effective_weight=0.2,
    )
    rows = [*[_row(d, 10.0) for d in _days(58)], older, _thin_row(date(2026, 10, 12))]
    payload = mscs.composite_hero_payload(rows)

    assert payload["as_of"] == date(2026, 10, 9), "薄行不得占据 as_of"
    assert payload["value"] == 44.5068
    assert payload["leg_count"] == 10
    assert payload["effective_weight"] == 0.88
    # 薄行的 extra 一个键都不许漏进响应（直读的是**选中行**）
    assert payload["value"] != 32.8157


def test_composite_hero_percentile_window_stops_at_selected_row() -> None:
    """分位窗口**截到选中行为止**：不能把选中行之后的中间值当成"今天".

    70 个齐备日（值 1..70）+ 两个薄行（999、30）：
    - 正确（截断）：样本 = 1..70，n=70，末值 70 是最大值 ⇒ `percentile=100.0`；
    - 出错（不截断）：样本 = 尾部 70 个 = 3..70,999,30，末值 30 ⇒ `percentile=40.0`。
    断言两者可区分 —— 这就是"薄行不得污染今天的分位"。
    """
    rows = [_full_row(date(2026, 8, 1) + timedelta(days=i), float(i + 1)) for i in range(70)]
    rows.append(_thin_row(date(2026, 10, 12), 999.0))
    rows.append(_thin_row(date(2026, 10, 13), 30.0))
    payload = mscs.composite_hero_payload(rows, window=70)

    assert payload["as_of"] == date(2026, 8, 1) + timedelta(days=69)
    assert payload["value"] == 70.0
    assert payload["sample_count"] == 70  # 不截断会是 72 → 尾窗 70 但末值不同
    assert payload["percentile"] == 100.0


def test_composite_hero_payload_absent_extra_is_null_not_invented() -> None:
    """老行（`extra` 无这些键）→ **不可发布**：权重说不清的行不作头条，字段 `null` 不发明.

    一条可发布的都没有时 `as_of` 也是 `None` —— 这是**响亮**的空，
    好过拿一个权重不明的数字当"今天的情绪"。
    """
    rows = [_row(d, 10.0) for d in _days(2)]
    rows.append(_row(date(2026, 1, 1), 12.0, caliber=None))  # extra=None 的最老行

    payload = mscs.composite_hero_payload(rows)

    assert payload["as_of"] is None
    assert payload["value"] is None
    assert payload["percentile"] is None
    assert payload["legs"] is None
    assert payload["leg_count"] == 0
    assert payload["total_legs"] == 0
    assert payload["effective_weight"] is None
    assert payload["min_effective_weight"] is None
    assert payload["caliber_version"] is None


def test_composite_hero_payload_null_today_value_has_no_percentile() -> None:
    """过闸门的行落 NULL（合成日缺数）→ `value=null` 且 `percentile=null`；
    `sample_count` 仍如实报尾部有效样本数（60 ≥ 60 → `insufficient_history=false`）。

    这是**防御分支**：权重 ≥ 0.80 蕴含"至少 7 条腿"，实际很难同时缺数，
    但读面不得因为"权重够"就把 NULL 当成分位对象。
    """
    rows = [_row(d, 10.0) for d in _days(60)]
    rows.append(_full_row(date(2026, 10, 12), None))
    payload = mscs.composite_hero_payload(rows)

    assert payload["as_of"] == date(2026, 10, 12)
    assert payload["value"] is None
    assert payload["percentile"] is None
    assert payload["sample_count"] == 60
    assert payload["insufficient_history"] is False


def test_composite_hero_payload_empty_series() -> None:
    """一行都没有 → 全空 + `insufficient_history=true`（不是 500、不是 0 分位）。"""
    payload = mscs.composite_hero_payload([])
    assert set(payload) == CONTRACT_KEYS
    assert payload["as_of"] is None
    assert payload["value"] is None
    assert payload["sample_count"] == 0
    assert payload["percentile"] is None
    assert payload["insufficient_history"] is True
    assert payload["window"] == CONTRACT_WINDOW
    assert payload["legs"] is None


def test_composite_hero_payload_short_history_truncated_window() -> None:
    """大窗口打在小序列上（in-process）：`window=1260` 但只有 30 个样本 → 不足。"""
    payload = mscs.composite_hero_payload([_full_row(d, 10.0) for d in _days(30)])
    assert payload["window"] == CONTRACT_WINDOW
    assert payload["sample_count"] == 30
    assert payload["percentile"] is None
    assert payload["insufficient_history"] is True


# ---------------------------------------------------------------------------
# 3. 端点级：匿名 200 + 契约键 + 样本不足
# ---------------------------------------------------------------------------


class _FakeSessionFactory:
    """`async_session_factory()` 替身（默认门禁绝不触 PG）。"""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def __aenter__(self) -> Any:
        return self._db

    async def __aexit__(self, *exc: Any) -> None:
        return None


def _patch_composite_read(
    monkeypatch: pytest.MonkeyPatch,
    rows: list[Any],
    *,
    expected_as_of: date | None = None,
    lag: int | None = 0,
) -> list[tuple]:
    """把 hero 的读路径换成桩，并记录 `(industry_key, metric_key)` 供断言。

    `expected_as_of` 默认 `None` ⇒ 模拟"日历判不出期望日"（缓存未命中 / 覆盖不到今天），
    此时新鲜度三字段必须留 `None` 而**不是**假装新鲜。
    """
    calls: list[tuple] = []

    async def _fake_history(db: Any, industry_key: str, metric_key: str, limit: int = 240):
        calls.append((industry_key, metric_key, limit))
        return list(rows)

    from app.services import market_day_service

    async def _fake_details(cache: Any, *, as_of: date | None, now: Any = None):
        """新鲜度接缝（2026-10-17 换锚）：日历口径的期望日 + **日历计数**的落后天数.

        换锚的理由：旧实现用 `max(daily_quotes)` 当"应该到哪天"，与滞后的数据链同源
        ⇒ "今天整天没采"时期望日跟着退回昨天、`stale` 恒为 `false`（自指）。
        """
        if expected_as_of is None:
            return None, None, "calendar_cache_miss"
        return expected_as_of, (None if lag is None else lag), None

    monkeypatch.setattr(mscs, "async_session_factory", lambda: _FakeSessionFactory(object()))
    monkeypatch.setattr(industry_metric_repo, "get_metric_history", _fake_history)
    monkeypatch.setattr(market_day_service, "resolve_expected_day_details", _fake_details)
    return calls


async def test_composite_endpoint_anonymous_200_contract_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**匿名**（无 cookie/token）200；响应键逐字等于契约；`window` 恒 1260。"""
    rows = [_row(d, 20.0 + i) for i, d in enumerate(_days(59))]  # 20..78
    rows.append(_full_row(date(2026, 10, 12), Decimal("32.8157")))
    calls = _patch_composite_read(monkeypatch, rows, expected_as_of=date(2026, 10, 12))

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/sentiment/composite")  # 不带任何认证头

    assert resp.status_code == 200, resp.text
    body = resp.json()
    # 端点响应比 hero payload 多一个 `provisional`（`schemas/limit_up.py` 的 `bool | None`）：
    # 合并基线后 schema 已带该键而用例未跟上。仍是**全等**断言，只把已知新增键显式列出。
    assert set(body) == CONTRACT_KEYS | {"provisional"}
    assert body["as_of"] == "2026-10-12"
    assert body["value"] == 32.8157
    assert body["window"] == CONTRACT_WINDOW
    assert body["sample_count"] == 60
    assert body["percentile"] == round(100 * 14 / 60, 2)
    assert body["insufficient_history"] is False
    assert body["legs"] == ALL_LEGS
    assert body["leg_count"] == 11
    assert body["total_legs"] == 11
    assert body["effective_weight"] == 1.0
    assert body["min_effective_weight"] == 0.2
    assert body["caliber_version"] == "s1.13-v1"
    # 新鲜度：本地判定追平（as_of == expected_as_of）⇒ 不 stale
    assert body["expected_as_of"] == "2026-10-12"
    assert body["lag_trading_days"] == 0
    assert body["stale"] is False

    assert calls and calls[0][0] == "market"
    assert calls[0][1] == "market_sentiment_composite"


async def test_composite_endpoint_stale_when_thin_run_ahead(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**源侧超前**（薄行跑在齐备日之前）⇒ 退回齐备日 + `stale=true` + 落后交易日数.

    这正是用户报的场景：薄行（3 条腿）比齐备日新，读面必须展示齐备日并如实报落后。
    """
    rows = [_row(d, 10.0) for d in _days(58)]
    rows.append(
        _row(
            date(2026, 10, 9),
            44.5068,
            participants=[f"leg_{i}" for i in range(10)],
            n_constituents=11,
            effective_weight_sum=0.88,
            min_effective_weight=0.2,
        )
    )
    rows.append(_thin_row(date(2026, 10, 12)))
    _patch_composite_read(monkeypatch, rows, expected_as_of=date(2026, 10, 12), lag=1)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/sentiment/composite")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["as_of"] == "2026-10-09"
    assert body["leg_count"] == 10
    assert body["effective_weight"] == 0.88
    assert body["expected_as_of"] == "2026-10-12"
    assert body["lag_trading_days"] == 1
    assert body["stale"] is True


async def test_composite_endpoint_freshness_unknown_is_not_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判不出最新完整交易日 ⇒ 三字段 `null`（`stale=null` **不得**被读成"新鲜"）."""
    _patch_composite_read(monkeypatch, [_full_row(d, 10.0) for d in _days(30)], expected_as_of=None)

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/sentiment/composite")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["stale"] is None
    assert body["lag_trading_days"] is None
    assert body["expected_as_of"] is None
    # 主数据不受新鲜度缺失影响
    assert body["as_of"] == _days(30)[-1].isoformat()


async def test_count_trading_days_between_is_calendar_based() -> None:
    """落后交易日数**必须由日历算**，不得由 `daily_quotes` 实际持有日算.

    这条是"自指 bug 不许复活"的守卫：修 bug 的场景正是"今天整天没采"——行情表里
    根本没有今天，用持有日计数会得到 0 天 ⇒ 读面报 `stale=false`。
    """
    from app.services import market_day_service as mds

    cal = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
    # 选中行 10-15、期望日 10-16 ⇒ 落后 1 个交易日（即便行情表里一行 10-16 都没有）
    assert (
        mds.count_trading_days_between(cal, after=date(2026, 10, 15), upto=date(2026, 10, 16)) == 1
    )
    assert (
        mds.count_trading_days_between(cal, after=date(2026, 10, 13), upto=date(2026, 10, 16)) == 3
    )
    # 空区间 ⇒ 0（`after == upto` 是追平，不是落后一天）
    assert (
        mds.count_trading_days_between(cal, after=date(2026, 10, 16), upto=date(2026, 10, 16)) == 0
    )
    assert mds.count_trading_days_between([], after=date(2026, 10, 1), upto=date(2026, 10, 16)) == 0


async def test_composite_endpoint_insufficient_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """30 个样本（< 60）→ `insufficient_history=true` 且 `percentile=null`。"""
    _patch_composite_read(monkeypatch, [_full_row(d, 10.0) for d in _days(30)])

    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/market/sentiment/composite")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["sample_count"] == 30
    assert body["insufficient_history"] is True
    assert body["percentile"] is None
    assert body["window"] == CONTRACT_WINDOW


# ---------------------------------------------------------------------------
# 4. 真库（`-m e2e`）：只读一致性（默认 deselect）
# ---------------------------------------------------------------------------


@pytest.mark.e2e
async def test_composite_endpoint_real_db_consistency() -> None:
    """真库验收：`sample_count <= window`；不足时不给分位；够时给 0–100 的分位。"""
    from app.core.database import engine

    try:
        payload = await mscs.get_composite_hero()
    finally:
        await engine.dispose()

    assert payload["window"] == CONTRACT_WINDOW
    assert 0 <= payload["sample_count"] <= CONTRACT_WINDOW
    if payload["insufficient_history"]:
        assert payload["percentile"] is None
    elif payload["value"] is not None:
        assert payload["percentile"] is not None
        assert 0.0 <= payload["percentile"] <= 100.0
    if payload["legs"] is not None:
        assert payload["leg_count"] == len(payload["legs"])


# ─────────────────────────────────────────────────────────────────────────────
# 2026-10-17：`provisional`（临时值三态）+ 新鲜度换锚
# ─────────────────────────────────────────────────────────────────────────────

#: 缺 T+1 融资融券两条腿的参与集（真实形态：盘后读到的当期值就长这样，权重 0.84）。
LEGS_WITHOUT_LATE_ARRIVAL = [
    leg for leg in ALL_LEGS if leg not in mscs.registry.COMPOSITE_LATE_ARRIVAL_KEYS
]


def test_provisional_window_matches_converge_window() -> None:
    """读面的"仍会被改写"窗口必须与收敛器的重算窗口同值（跨模块单一真源）。

    两个数一旦分叉：窗口更宽 ⇒ 历史行自称"临时"；更窄 ⇒ 当期值自称"终值"，
    而它下一轮就会被改写 —— 两种都是对读者的谎。故用测试钉住。
    """
    from app.services import derived_metrics_service

    assert (
        mscs.COMPOSITE_PROVISIONAL_WINDOW_TRADING_DAYS
        == derived_metrics_service.CONVERGE_WINDOW_TRADING_DAYS
    )


def test_late_arrival_keys_are_declared_in_the_registry() -> None:
    """T+1 成分必须来自**登记**（不是按权重猜）：融资融券两条腿。"""
    assert mscs.registry.COMPOSITE_LATE_ARRIVAL_KEYS == frozenset(
        {"margin_financing_balance", "margin_financing_buy_ratio"}
    )


@pytest.mark.parametrize(
    ("participants", "lag", "expected"),
    [
        # 缺 T+1 腿 + 仍在窗口内 ⇒ 值会被改写
        (LEGS_WITHOUT_LATE_ARRIVAL, 0, True),
        (LEGS_WITHOUT_LATE_ARRIVAL, 9, True),
        # 窗口之外 ⇒ 收敛器再也不会碰它 ⇒ 终值
        (LEGS_WITHOUT_LATE_ARRIVAL, 10, False),
        # 腿齐 ⇒ 终值
        (ALL_LEGS, 0, False),
        # 判不出：没登记参与集 / 没有日历
        (None, 0, None),
        (LEGS_WITHOUT_LATE_ARRIVAL, None, None),
    ],
)
def test_composite_is_provisional_tristate(
    participants: list[str] | None, lag: int | None, expected: bool | None
) -> None:
    assert mscs.composite_is_provisional(participants, lag_trading_days=lag) is expected


def test_hero_payload_marks_provisional_for_late_arrival_row() -> None:
    """当期值缺融资融券且刚发 ⇒ `provisional=True`（页面据此明写"会被重算"）。"""
    rows = [
        _full_row(_days(30)[0]),
        _row(_days(30)[1], 27.5, participants=LEGS_WITHOUT_LATE_ARRIVAL, effective_weight_sum=0.84),
    ]

    payload = mscs.composite_hero_payload(rows, expected_as_of=_days(30)[1], lag_trading_days=0)

    assert payload["provisional"] is True
    assert payload["leg_count"] == 9


def test_hero_payload_provisional_false_when_outside_window() -> None:
    """落后超过改写窗口 ⇒ 那行再也不会被重算 ⇒ `provisional=False`（是终值）。"""
    rows = [
        _row(_days(30)[0], 27.5, participants=LEGS_WITHOUT_LATE_ARRIVAL, effective_weight_sum=0.84)
    ]

    payload = mscs.composite_hero_payload(rows, expected_as_of=_days(30)[0], lag_trading_days=30)

    assert payload["provisional"] is False


def test_hero_payload_provisional_none_is_not_false() -> None:
    """判不出必须是 `None`，**不得**塌成 `False`（那会把"不知道"说成"是终值"）。"""
    rows = [_full_row(_days(30)[0])]

    payload = mscs.composite_hero_payload(rows)  # 没有 expected_as_of / lag

    assert payload["provisional"] is None
    assert payload["stale"] is None


class _CalendarCache:
    """只放交易日历的缓存替身（键与载荷形状与 `market_day_service` 契约一致）。"""

    def __init__(self, days: list[date], covered_through: date) -> None:
        self.store = {mds.CALENDAR_CACHE_KEY: mds.calendar_cache_payload(days, covered_through)}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.store[key] = value


async def test_hero_reports_stale_when_today_was_never_ingested(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**旗舰回归**：今天整天没采 ⇒ `stale=true`、`lag_trading_days=1`。

    这就是 2026-10-16 用户看到的现象（16 号晚页面显示 15 号）在旧实现下的伴随症状：
    旧锚取自 `max(daily_quotes)`，与"没采到"同源 ⇒ 期望日跟着退回 15 号 ⇒ `stale=false`，
    页面于是**既显示旧数据又声称新鲜**。新锚走外部日历：行情表里没有 16 号也照样
    给出 `expected_as_of=16`、`lag=1`。
    """
    from app.services import market_day_service as mds_mod

    cal = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
    cache = _CalendarCache(cal, covered_through=date(2026, 10, 16))
    # 库里最新只到 10-15；10-16 一行都没有（真实事故形态）
    rows = [_full_row(date(2026, 10, 14)), _full_row(date(2026, 10, 15))]

    async def _fake_history(db: Any, industry_key: str, metric_key: str, limit: int = 240):
        return list(rows)

    monkeypatch.setattr(mscs, "async_session_factory", lambda: _FakeSessionFactory(object()))
    monkeypatch.setattr(industry_metric_repo, "get_metric_history", _fake_history)
    # 时钟：10-16 晚 22:30（已过 cutoff）⇒ 期望日就是 10-16
    monkeypatch.setattr(
        mds_mod,
        "now_sh",
        lambda now=None: datetime(2026, 10, 16, 22, 30, tzinfo=ZoneInfo("Asia/Shanghai")),
    )

    payload = await mscs.get_composite_hero(cache=cache)

    assert payload["as_of"] == date(2026, 10, 15)
    assert payload["expected_as_of"] == date(2026, 10, 16)
    assert payload["lag_trading_days"] == 1
    assert payload["stale"] is True


def test_hero_latest_withheld_explains_the_blocked_day() -> None:
    """**2026-10-19 事故回归**：当日有行但没过发布闸门 ⇒ 读面必须能说清"为什么不是它"。

    事故形态：17:45 收敛算出 8/11 条腿、生效权重 0.72（缺两融 T+1 ×2 + QVIX 源滞后），
    被 0.80 闸门拦下；而 hero 只回选中的 10-16，前端拿不到被拦原因，只好把
    `expected_as_of`（日历期望日 10-19）写成"各成分指标齐备的数据已到 10-19"。
    """
    thin = _row(
        date(2026, 10, 19),
        None,
        participants=[k for k in ALL_LEGS if k not in _MISSING_1019],
        n_constituents=11,
        effective_weight_sum=0.72,
        min_effective_weight=0.2,
    )
    thin.extra["missing_reason"] = "below_publish_weight"
    thin.extra["constituents"] = {
        "qvix_50etf": {"reason": "source_not_updated", "participated": False},
        "margin_financing_balance": {"reason": "source_empty", "participated": False},
        "margin_financing_buy_ratio": {"reason": "source_empty", "participated": False},
    }

    payload = mscs.composite_hero_payload(
        [_full_row(date(2026, 10, 16)), thin],
        expected_as_of=date(2026, 10, 19),
        lag_trading_days=1,
    )

    assert payload["as_of"] == date(2026, 10, 16)
    assert payload["stale"] is True
    withheld = payload["latest_withheld"]
    assert withheld is not None, "有更新的被拦行却没交代 —— 页面只能编文案"
    assert withheld["as_of"] == date(2026, 10, 19)
    assert withheld["leg_count"] == 8
    assert withheld["total_legs"] == 11
    assert withheld["effective_weight"] == pytest.approx(0.72)
    assert withheld["min_effective_weight"] == pytest.approx(mscs.PUBLISH_MIN_WEIGHT)
    assert withheld["missing_reason"] == "below_publish_weight"
    by_key = {m["key"]: m for m in withheld["missing"] or []}
    assert set(by_key) == set(_MISSING_1019)
    # 迟到属性来自 registry（两融=T+1），不是按权重猜的
    assert by_key["margin_financing_balance"]["late_arrival"] is True
    assert by_key["qvix_50etf"]["late_arrival"] is False
    assert by_key["qvix_50etf"]["reason"] == "source_not_updated"


def test_hero_latest_withheld_is_none_when_newest_row_published() -> None:
    """最新一行就是上屏行 ⇒ `latest_withheld=None`（正常态不制造噪声）。"""
    payload = mscs.composite_hero_payload(
        [_full_row(date(2026, 10, 16)), _full_row(date(2026, 10, 19))],
        expected_as_of=date(2026, 10, 19),
        lag_trading_days=0,
    )

    assert payload["as_of"] == date(2026, 10, 19)
    assert payload["latest_withheld"] is None


async def test_hero_second_call_is_served_from_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """hero 有整包缓存：第二次 GET 不得再读库。

    为什么必须有：全量历史 4007 行经 ORM 载入要 0.3–0.5s（2026-10-19 生产实测，
    裸 SQL 19ms —— 差距在 `extra` JSONB 解码与 4000 个模型实例），而这是首屏 GET。
    断言方式 = 数 `get_metric_history` 的调用次数（**不关心耗时**，耗时在 CI 里不稳）。
    缓存键/载荷形状由 `CacheClient`（orjson）决定，真 Redis 下日期会变 ISO 字符串 ——
    故这里同时断言缓存值能被响应模型解析，避免"缓存命中即 500"。
    """
    from app.schemas.limit_up import SentimentCompositeOut

    cal = [date(2026, 10, 13), date(2026, 10, 14), date(2026, 10, 15), date(2026, 10, 16)]
    cache = _CalendarCache(cal, covered_through=date(2026, 10, 16))
    rows = [_full_row(date(2026, 10, 15)), _full_row(date(2026, 10, 16))]
    calls = {"n": 0}

    async def _fake_history(db: Any, industry_key: str, metric_key: str, limit: int = 240):
        calls["n"] += 1
        return list(rows)

    monkeypatch.setattr(mscs, "async_session_factory", lambda: _FakeSessionFactory(object()))
    monkeypatch.setattr(industry_metric_repo, "get_metric_history", _fake_history)

    first = await mscs.get_composite_hero(cache=cache)
    second = await mscs.get_composite_hero(cache=cache)

    assert calls["n"] == 1, "第二次调用不应再读库（缓存未命中说明 TTL/键写漏了）"
    assert second == first
    assert mscs.COMPOSITE_HERO_CACHE_KEY in cache.store
    # 缓存载荷必须能被响应模型解析（真 Redis 下 as_of 是 ISO 字符串）
    SentimentCompositeOut.model_validate(second)
