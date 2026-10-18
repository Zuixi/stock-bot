"""情绪阶段（启发式规则）与情绪脉搏（small multiples / 指数对比）纯函数测试。

重点不是"规则好不好"，而是三条可证伪的纪律：

1. **PIT**：第 i 天的分位只用当天及之前的样本，追加未来数据不得改历史；
2. **持续性**：阶段不能按天翻转（否则背景带是噪声），但**最后一天以原始判定为准**；
3. **样本门槛**：n<5 的值仍展示，但不进统计/分位/分布；指数对比与阶段共用同一把 PIT 尺子。
"""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any

from app.services import sentiment_pulse_service as sps
from app.services import sentiment_regime_service as srs

D0 = date(2026, 9, 1)


def _row(
    i: int,
    *,
    zt: int = 40,
    zb: int = 10,
    dt: int = 3,
    broken: float | None = 0.2,
    streak: int = 4,
    promo: float | None = 0.3,
    promo_n: int = 20,
) -> SimpleNamespace:
    return SimpleNamespace(
        trade_date=D0 + timedelta(days=i),
        zt_count=zt,
        dt_count=dt,
        zb_count=zb,
        broken_rate=broken,
        yzt_avg_pct=1.0,
        yzt_avg_open_premium=0.5,
        yzt_n=20,
        promo_1to2=promo,
        promo_1to2_n=promo_n,
        promo_2to3=0.4,
        promo_2to3_n=8,
        max_streak=streak,
    )


# ---------------------------------------------------------------------------
# 1. 分位 PIT 与阶段规则
# ---------------------------------------------------------------------------


def test_trailing_percentiles_are_pit() -> None:
    """第 i 天的分位只看 `[i-window+1, i]`；追加未来值不改历史。"""
    base = [float(i) for i in range(1, 41)]
    before = srs.trailing_percentiles(base)
    after = srs.trailing_percentiles(base + [999.0])
    assert before == after[: len(base)]
    # 前 19 天样本不足 → None；第 20 天起有值
    assert before[18] is None
    assert before[19] is not None


def test_classify_boundaries() -> None:
    """规则边界逐条钉住（改阈值必须改这里 + 版本号）。"""

    def stage(zt, zt_pct, broken_pct, streak=5, streak_pct=50):
        return srs.classify(
            zt_count=zt,
            zt_pct=zt_pct,
            broken_pct=broken_pct,
            max_streak=streak,
            streak_pct=streak_pct,
        )

    assert stage(30, 10, 40) == "ice"
    assert stage(60, 30, 70) == "ebb"
    assert stage(60, 30, 50) == "repair"
    assert stage(120, 90, 40, streak=9, streak_pct=85) == "climax"
    assert stage(60, 60, 40) == "ferment"
    assert stage(60, 60, 70) == "mixed"
    # 低位横盘：分位饱和到 100，但绝对水位不够 → 不能判高潮
    assert stage(20, 100, 100) == "mixed"
    assert (
        srs.classify(zt_count=None, zt_pct=None, broken_pct=70, max_streak=5, streak_pct=50)
        == "unknown"
    )


def test_build_regime_merges_segments_and_keeps_current_raw() -> None:
    """连续同阶段合并；最后一天必须反映当日原始判定（不能被平滑吞掉）。"""
    # 25 天：前半 zt 低/炸板高（ebb），最后一天 zt 高/炸板低（ferment）
    rows = [_row(i, zt=20, zb=30, broken=0.7) for i in range(24)]
    rows.append(_row(24, zt=120, zb=2, broken=0.05, streak=9))
    series = [
        {
            "trade_date": r.trade_date,
            "zt_count": r.zt_count,
            "dt_count": r.dt_count,
            "zb_count": r.zb_count,
            "broken_rate": r.broken_rate,
            "yzt_avg_pct": r.yzt_avg_pct,
            "yzt_avg_open_premium": r.yzt_avg_open_premium,
            "yzt_n": r.yzt_n,
            "promo_1to2": r.promo_1to2,
            "promo_1to2_n": r.promo_1to2_n,
            "promo_2to3": r.promo_2to3,
            "promo_2to3_n": r.promo_2to3_n,
            "max_streak": r.max_streak,
        }
        for r in rows
    ]
    out = srs.build_regime(series)
    # zt=120 / 炸板 5% / 最高板 9 → 三维都到极值，判"高潮"（规则里 climax 在 ferment 之前）
    assert out["current"]["stage"] == "climax"
    assert out["current"]["since"] == rows[-1].trade_date
    assert any(s["stage"] == "mixed" for s in out["segments"])
    assert out["segments"][-1]["days"] == 1


# ---------------------------------------------------------------------------
# 2. 样本门槛 / Wilson
# ---------------------------------------------------------------------------


def test_low_sample_is_flagged_but_value_kept() -> None:
    from app.services import sentiment_metric_service as sms

    row = _row(0, promo=0.5, promo_n=2)
    payload = sms.metric_payload(
        [row], "promo_1to2", as_of=row.trade_date, as_of_quality="complete", days=1
    )
    assert payload["latest"]["value"] == 0.5
    assert payload["latest"]["low_sample"] is True
    assert payload["stats"] is None
    assert payload["percentile_rank"] is None
    assert payload["sample_gate"]["excluded_days"] == 1


def test_wilson_interval_brackets_point_estimate() -> None:
    from app.services import sentiment_metric_service as sms

    row = _row(0, promo=0.4, promo_n=20)
    payload = sms.metric_payload(
        [row], "promo_1to2", as_of=row.trade_date, as_of_quality="complete", days=1
    )
    interval = payload["latest_interval"]
    assert interval["method"] == "wilson"
    assert interval["n"] == 20
    assert interval["low"] < 0.4 < interval["high"]


# ---------------------------------------------------------------------------
# 3. 情绪脉搏：指数对齐 / 背离 / 时间窗口
# ---------------------------------------------------------------------------


def _index_rows(n: int = 60, start: date = D0 - timedelta(days=30)) -> list[SimpleNamespace]:
    return [
        SimpleNamespace(trade_date=start + timedelta(days=i), close=100.0 + i) for i in range(n)
    ]


def test_pulse_payload_aligns_benchmark_and_windows() -> None:
    rows = [_row(i, zt=20 + i, broken=0.5, streak=3 + i // 10) for i in range(60)]
    payload = sps.build_pulse_payload(
        rows,
        days=20,
        benchmark_code="399006.SZ",
        benchmark_name="创业板指",
        benchmark_rows=_index_rows(),
        as_of=rows[-1].trade_date,
        as_of_quality="complete",
    )
    assert payload["window"]["samples"] == 20
    assert len(payload["series"]) == 20
    assert len(payload["benchmark"]["points"]) == 20
    assert payload["regime"]["current"] is not None
    assert len(payload["comparison"]) == 20


def test_divergence_runs_require_three_days() -> None:
    comparison = [
        {"trade_date": D0 + timedelta(days=i), "sentiment_pct": s, "index_pct": x}
        for i, (s, x) in enumerate([(80, 10), (80, 10), (80, 10), (50, 50), (10, 80), (10, 80)])
    ]
    runs = sps._divergence_runs(comparison)
    assert len(runs) == 1
    assert runs[0]["kind"] == "sentiment_stronger"
    assert runs[0]["days"] == 3
    assert runs[0]["from"] == comparison[0]["trade_date"]


def test_index_5d_series_uses_own_previous_rows() -> None:
    rows = _index_rows(10)
    values = sps._index_5d_series(rows)
    assert values[:5] == [None] * 5
    # close 从 100+i 起：第 5 行相对第 0 行 = 5%
    assert round(values[5], 4) == 5.0


# ---------------------------------------------------------------------------
# 4. 端点级：pulse 200 形状 / 未知 benchmark 404
# ---------------------------------------------------------------------------


async def test_pulse_endpoint_shape(monkeypatch: Any) -> None:
    import httpx
    from httpx import ASGITransport

    from app.api.deps import get_cache
    from app.main import app

    rows = [_row(i, zt=20 + i, broken=0.5, streak=3 + i // 10) for i in range(40)]
    payload = sps.build_pulse_payload(
        rows,
        days=20,
        benchmark_code="399006.SZ",
        benchmark_name="创业板指",
        benchmark_rows=_index_rows(70, start=D0 - timedelta(days=40)),
        as_of=rows[-1].trade_date,
        as_of_quality="complete",
    )

    async def _fake_get_pulse(cache: Any, *, days: int, benchmark_code: str) -> dict[str, Any]:
        return payload

    monkeypatch.setattr(sps, "get_pulse", _fake_get_pulse)

    async def _cache_dep() -> Any:
        return None

    app.dependency_overrides[get_cache] = _cache_dep
    try:
        async with httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/v1/market/sentiment/pulse", params={"days": 20})
            assert resp.status_code == 200, resp.text
            body = resp.json()
            assert body["window"]["samples"] == 20
            assert body["regime"]["current"]["stage"]
            assert body["benchmark"]["name"] == "创业板指"
            assert len(body["comparison"]) == 20

            async def _raise(*_a: Any, **_kw: Any) -> Any:
                raise KeyError("bad")

            monkeypatch.setattr(sps, "get_pulse", _raise)
            bad = await client.get(
                "/api/v1/market/sentiment/pulse", params={"benchmark": "000852.SH"}
            )
            assert bad.status_code == 404, bad.text
    finally:
        app.dependency_overrides.clear()
