"""情绪脉搏：把 9 个情绪指标、周期阶段、指数对比压进一次读面。

前端「情绪脉搏」区块消费本模块（`GET /market/sentiment/pulse`）：

- **small multiples**：9 个指标的逐日序列（与 `/sentiment/trends` 同源同投影）；
- **周期阶段**：`sentiment_regime_service.build_regime`（PIT、规则透明）；
- **指数对比**：创业板指/上证 5 日收益分位 vs 涨停家数分位，标出连续背离段；
- **时间范围**：`days`（默认 60，上限 250）；阶段分位始终用 250 日窗口，避免"窗口越长阶段越不同"。

读时计算、不物化；Redis 缓存 900s，落库时按前缀失效。指数数据来自 `index_dailies`
（本地只落了 `000001.SH` / `399006.SZ`，没有中证1000，故不提供 000852.SH）。
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from app.core.database import async_session_factory
from app.repositories import index_repo, limit_up_repo
from app.services import sentiment_regime_service as regime_svc
from app.services.limit_up_service import sentiment_series_row

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

PULSE_TTL = 900
PULSE_CACHE_PREFIX = "market:limit-up:pulse"

#: 允许的对比基准（本地 `index_dailies` 实际覆盖的两个；中证1000 未落库）。
BENCHMARKS: dict[str, str] = {
    "399006.SZ": "创业板指",
    "000001.SH": "上证指数",
}
DEFAULT_BENCHMARK = "399006.SZ"

#: 背离判定：两条分位差 ≥ 30 且连续 ≥ 3 个交易日（避免单日噪声）。
DIVERGENCE_GAP = 30.0
DIVERGENCE_MIN_DAYS = 3


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


def _index_5d_series(rows: list[Any]) -> list[float | None]:
    """指数 5 个交易日收益（%），与 `rows` 逐行对齐；不足 6 行 → None。"""
    closes = [float(r.close) for r in rows]
    out: list[float | None] = []
    for i in range(len(rows)):
        if i < 5 or closes[i - 5] == 0:
            out.append(None)
        else:
            out.append(round((closes[i] / closes[i - 5] - 1) * 100, 4))
    return out


def _pit_percentile_map(dates: list[date], values: list[float | None]) -> dict[date, float | None]:
    """PIT 分位（250 日窗口，`<=` 口径）：与阶段用同一把尺子，不随展示天数漂移。"""
    pcts = regime_svc.trailing_percentiles(values)
    return {d: pcts[i] for i, d in enumerate(dates)}


def _divergence_runs(
    comparison: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """连续背离段：`|sentiment_pct - index_pct| ≥ 30` 且连续 ≥ 3 日。"""
    runs: list[dict[str, Any]] = []
    start: int | None = None
    for i, point in enumerate(comparison + [None]):  # type: ignore[list-item]
        active = False
        if point is not None:
            s, x = point.get("sentiment_pct"), point.get("index_pct")
            active = s is not None and x is not None and abs(s - x) >= DIVERGENCE_GAP
        if active and start is None:
            start = i
        if not active and start is not None:
            days = i - start
            if days >= DIVERGENCE_MIN_DAYS:
                first = comparison[start]
                last = comparison[i - 1]
                kind = (
                    "sentiment_stronger"
                    if (first["sentiment_pct"] or 0) > (first["index_pct"] or 0)
                    else "index_stronger"
                )
                runs.append(
                    {
                        "from": first["trade_date"],
                        "to": last["trade_date"],
                        "days": days,
                        "kind": kind,
                        "label": "情绪强于指数" if kind == "sentiment_stronger" else "指数强于情绪",
                    }
                )
            start = None
    return runs


def build_pulse_payload(
    rows: list[Any],
    *,
    days: int,
    benchmark_code: str,
    benchmark_name: str,
    benchmark_rows: list[Any],
    as_of: date | None,
    as_of_quality: str,
) -> dict[str, Any]:
    """纯函数：组装 pulse 响应（无 IO；缓存与查询在 :func:`get_pulse`）。"""
    series_all = [sentiment_series_row(r) for r in rows]
    series_all.sort(key=lambda r: r["trade_date"])
    display = series_all[-days:] if days > 0 else []
    regime = regime_svc.build_regime(series_all)

    index_5d = _index_5d_series(benchmark_rows)
    index_5d_by_date = {r.trade_date: index_5d[i] for i, r in enumerate(benchmark_rows)}
    index_close = {r.trade_date: float(r.close) for r in benchmark_rows}
    benchmark_points: list[dict[str, Any]] = [
        {
            "trade_date": row["trade_date"],
            "close": index_close.get(row["trade_date"]),
            "pct_5d": index_5d_by_date.get(row["trade_date"]),
        }
        for row in display
    ]

    # 情绪/指数都用 **PIT 250 日分位**：与阶段同一把尺子，且不随 `days` 改变。
    zt_pct_all = regime_svc.trailing_percentiles([float(r["zt_count"]) for r in series_all])
    zt_pct_by_date = {row["trade_date"]: zt_pct_all[i] for i, row in enumerate(series_all)}
    index_pct_by_date = _pit_percentile_map([r.trade_date for r in benchmark_rows], index_5d)

    comparison: list[dict[str, Any]] = [
        {
            "trade_date": point["trade_date"],
            "sentiment_pct": zt_pct_by_date.get(point["trade_date"]),
            "index_pct": index_pct_by_date.get(point["trade_date"]),
            "index_pct_5d": point["pct_5d"],
        }
        for point in benchmark_points
    ]

    divergence = _divergence_runs(comparison)
    current_div = None
    if divergence and display and divergence[-1]["to"] == display[-1]["trade_date"]:
        current_div = divergence[-1]

    return {
        "benchmark": {
            "ts_code": benchmark_code,
            "name": benchmark_name,
            "points": benchmark_points,
        },
        "comparison": comparison,
        "divergence": divergence,
        "current_divergence": current_div,
        "as_of": as_of,
        "as_of_quality": as_of_quality,
        "window": {
            "days": days,
            "from": display[0]["trade_date"] if display else None,
            "to": display[-1]["trade_date"] if display else None,
            "samples": len(display),
        },
        "regime": regime,
        "series": display,
    }


def cache_key(days: int, benchmark_code: str) -> str:
    return f"{PULSE_CACHE_PREFIX}:{benchmark_code}:{days}"


async def get_pulse(
    cache: CacheClient | None,
    *,
    days: int = 60,
    benchmark_code: str = DEFAULT_BENCHMARK,
) -> dict[str, Any]:
    """`GET /market/sentiment/pulse` 的编排（读时计算 + Redis 缓存）。"""
    if benchmark_code not in BENCHMARKS:
        raise KeyError(benchmark_code)

    from app.services import market_day_service  # noqa: PLC0415

    key = cache_key(days, benchmark_code)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    db: AsyncSession
    async with async_session_factory() as db:
        # 阶段分位需要 250 日上下文：多取 250 行，展示只切最后 `days` 行。
        limit = min(500, days + regime_svc.REGIME_WINDOW)
        rows = await limit_up_repo.list_sentiment_days(db, limit)
        as_of = await limit_up_repo.latest_sentiment_day(db)
        market = await market_day_service.resolve_latest_complete_day(db)

        start = rows[0].trade_date if rows else None
        end = rows[-1].trade_date if rows else None
        benchmark_rows: list[Any] = []
        if start is not None and end is not None:
            benchmark_rows = await index_repo.get_kline(
                db,
                benchmark_code,
                start_date=start - timedelta(days=400),
                end_date=end,
            )

    quality = market.quality if market is not None else "partial"
    payload = build_pulse_payload(
        rows,
        days=days,
        benchmark_code=benchmark_code,
        benchmark_name=BENCHMARKS[benchmark_code],
        benchmark_rows=benchmark_rows,
        as_of=as_of,
        as_of_quality=quality,
    )
    if cache is not None and payload["window"]["samples"]:
        await cache.set(key, payload, ttl=PULSE_TTL)
    return payload
