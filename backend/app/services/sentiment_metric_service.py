"""情绪指标详情：把「情绪温度计」的 9 张卡各自变成可追踪的历史序列。

背景（2026-10-18 用户实测）：温度计能读"今天"，但每张卡都进不去历史 —— 缺的不是
数据（`market_sentiment_daily` 已按交易日冻结 240+ 天），而是**读面**。本模块只做读：

- **不物化**（无新表、无新 Job、无写路径）：窗口 ≤ 500 行，统计量现算；
- **与 `/sentiment/trends` 同一条口径**：NULL 不进样本、样本 < 60 不给分位、不插值；
- **口径文本不在此新编**：`METRIC_DEFS[*].caliber` 是
  `docs/design/limit-up-sentiment.md` §3 已裁定原文的删减，改口径须两处一起改。

端点：`GET /market/sentiment/metrics/{metric_key}?days=250`（公开，与 calendar/trends
同一 auth 姿态）。
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from app.core.database import async_session_factory
from app.repositories import limit_up_repo
from app.schemas.limit_up import MIN_PERCENTILE_SAMPLES, SENTIMENT_METRIC_KEYS

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.core.redis import CacheClient

#: 指标详情缓存 TTL（秒）。与 `/sentiment/trends` 同量级：读面是日频，5 分钟足够新鲜；
#: 落库时由 `limit_up_service` 的失效钩子按前缀删除。
METRIC_TTL = 900
_METRIC_CACHE_PREFIX = "market:limit-up:metric"
#: 统计量/分布的最低有效样本：低于此值只画点，不给"区间/分布"这类会被读成事实的图形。
STATS_MIN_SAMPLES = 5
DISTRIBUTION_MIN_SAMPLES = 10
#: 直方图箱数（等宽）。10 箱在 250 个样本下每箱期望 20+，不会被单点抖动带偏。
DISTRIBUTION_BINS = 10
#: 样本型指标的最小有效样本：低于 5 的日子不进统计量/分位/分布（n=2 的 50% 不是信号）。
MIN_SAMPLE_N = 5
#: Wilson 区间的 95% 正态分位。
WILSON_Z = 1.96
#: 需要给 Wilson 区间的比值型指标。
RATIO_METRICS = frozenset({"broken_rate", "promo_1to2", "promo_2to3"})


@dataclass(frozen=True)
class MetricDef:
    """一个指标的读面定义（唯一真相：口径、量纲、样本列、方向）。"""

    key: str
    label: str
    unit: Literal["count", "board", "ratio", "pct"]
    group: Literal["breadth", "money", "promotion", "space"]
    hotter_when: Literal["high", "low"]
    caliber: str
    #: 该指标的"样本数"落在哪一列；非样本型指标为 None（缺失 ≠ 0）
    sample_field: str | None = None


#: 9 张卡与 `SENTIMENT_METRIC_KEYS` 1:1。新增指标必须同时改 schema 常量、前端类型与
#: `docs/design/limit-up-sentiment.md` §3.6；顺序即详情页切换器的展示顺序。
METRIC_DEFS: dict[str, MetricDef] = {
    "zt_count": MetricDef(
        key="zt_count",
        label="涨停家数",
        unit="count",
        group="breadth",
        hotter_when="high",
        caliber=(
            "收盘价 ≥ 交易所涨停价（stock_price_limits.up_limit，容差 0.005）的个股数。"
            "判定恒来自交易所限价，不用名称/代码前缀推算。"
        ),
    ),
    "zb_count": MetricDef(
        key="zb_count",
        label="炸板家数",
        unit="count",
        group="breadth",
        hotter_when="low",
        caliber="盘中最高价触及涨停价、但收盘未封板的个股数（touched AND NOT is_lu）。",
    ),
    "dt_count": MetricDef(
        key="dt_count",
        label="跌停家数",
        unit="count",
        group="breadth",
        hotter_when="low",
        caliber="收盘价 ≤ 交易所跌停价的个股数（同一套限价口径）。",
    ),
    "broken_rate": MetricDef(
        key="broken_rate",
        label="炸板率",
        unit="ratio",
        group="promotion",
        hotter_when="low",
        caliber=(
            "炸板家数 /（涨停家数 + 炸板家数）。分母为 0 的交易日落 NULL（缺失 ≠ 0）"
            "，该日不进分位与统计样本。"
        ),
    ),
    "yzt_avg_pct": MetricDef(
        key="yzt_avg_pct",
        label="昨日涨停均值",
        unit="pct",
        group="money",
        hotter_when="high",
        caliber=(
            "前一交易日涨停股在当日的收盘涨跌幅均值（按交易所 pre_close 计）；"
            "当日停牌或 pre_close 缺失的票不计入。"
        ),
        sample_field="yzt_n",
    ),
    "yzt_avg_open_premium": MetricDef(
        key="yzt_avg_open_premium",
        label="今开溢价均值",
        unit="pct",
        group="money",
        hotter_when="high",
        caliber=(
            "前一交易日涨停股在当日的开盘溢价均值 = mean((open − pre_close) / pre_close)，"
            "单位 %；停牌票不计入，样本数见 yzt_n。"
        ),
        sample_field="yzt_n",
    ),
    "promo_1to2": MetricDef(
        key="promo_1to2",
        label="1进2晋级率",
        unit="ratio",
        group="promotion",
        hotter_when="high",
        caliber=(
            "昨日首板股今日收盘仍涨停的比例（交集式分母 = 昨日涨停且今日有行情的票）；"
            "分母为 0 落 NULL，样本 < 5 标「样本小」。"
        ),
        sample_field="promo_1to2_n",
    ),
    "promo_2to3": MetricDef(
        key="promo_2to3",
        label="2进3晋级率",
        unit="ratio",
        group="promotion",
        hotter_when="high",
        caliber=(
            "昨日 2 板股今日收盘晋级到 3 板及以上的比例；分母为 0 落 NULL，样本 < 5 标「样本小」。"
        ),
        sample_field="promo_2to3_n",
    ),
    "max_streak": MetricDef(
        key="max_streak",
        label="最高板",
        unit="board",
        group="space",
        hotter_when="high",
        caliber=(
            "当日连板梯队的最高连板数（截至当日）。连板由窗口内 is_lu 的 gaps-and-islands"
            " 计算，窗口起点截平会被缺失守卫挡掉。"
        ),
    ),
}

assert tuple(METRIC_DEFS) == SENTIMENT_METRIC_KEYS, (
    "METRIC_DEFS 的顺序/成员必须与 schema 的 SENTIMENT_METRIC_KEYS 一致"
)


def _sample_n(defn: MetricDef, row: dict[str, Any]) -> int | None:
    """该日样本数：炸板率的分母是 `涨停+炸板`；其余样本型指标读自己的 `*_n` 列。

    非样本型（涨停家数/最高板等）恒 `None` —— 不拿 0 冒充"样本 0 家"。
    """
    if defn.key == "broken_rate":
        zt, zb = row.get("zt_count"), row.get("zb_count")
        return int(zt) + int(zb) if zt is not None and zb is not None else None
    if defn.sample_field is not None:
        n = row.get(defn.sample_field)
        return int(n) if n is not None else None
    return None


def _raw_value(defn: MetricDef, row: dict[str, Any]) -> float | None:
    """原始值（不做样本门槛）：用于 `series` 展示，保证"当天确实发生了 50%"可见。"""
    value = row.get(defn.key)
    return None if value is None else float(value)


def _low_sample(defn: MetricDef, row: dict[str, Any]) -> bool:
    """样本是否小到不该进统计/分位（n<5）。值本身仍展示，只标"样本不足"。"""
    n = _sample_n(defn, row)
    return n is not None and n < MIN_SAMPLE_N


def _wilson(successes: int, n: int, z: float = WILSON_Z) -> dict[str, Any]:
    """二项比例的 Wilson 95% 区间（小样本下比正态近似可靠）。"""
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return {
        "low": round(max(0.0, centre - margin), 6),
        "high": round(min(1.0, centre + margin), 6),
        "level": 0.95,
        "n": n,
        "method": "wilson",
    }


def _nearest_rank(sorted_values: list[float], pct: int) -> float:
    """nearest-rank：升序第 `ceil(pct/100 × n)` 个（0 → 最小、100 → 最大）。"""
    n = len(sorted_values)
    rank = max(1, math.ceil(pct / 100 * n))
    return sorted_values[rank - 1]


def _stats(values: list[float]) -> dict[str, Any] | None:
    """窗口统计量；有效样本 < `STATS_MIN_SAMPLES` → `None`（不给会被读成事实的均值）。"""
    if len(values) < STATS_MIN_SAMPLES:
        return None
    ordered = sorted(values)
    return {
        "sample_count": len(ordered),
        "min": ordered[0],
        "max": ordered[-1],
        "mean": round(statistics.fmean(ordered), 6),
        "median": round(statistics.median(ordered), 6),
        "p10": _nearest_rank(ordered, 10),
        "p25": _nearest_rank(ordered, 25),
        "p75": _nearest_rank(ordered, 75),
        "p90": _nearest_rank(ordered, 90),
        "std": round(statistics.pstdev(ordered), 6),
    }


def _distribution(values: list[float]) -> list[dict[str, Any]]:
    """等宽直方图（10 箱）。样本 < 10 或全部同值 → 空/单箱（不画假分布）。"""
    if len(values) < DISTRIBUTION_MIN_SAMPLES:
        return []
    lo, hi = min(values), max(values)
    if hi == lo:
        return [{"from": lo, "to": lo, "count": len(values)}]
    width = (hi - lo) / DISTRIBUTION_BINS
    counts = [0] * DISTRIBUTION_BINS
    for v in values:
        idx = min(int((v - lo) / width), DISTRIBUTION_BINS - 1)
        counts[idx] += 1
    return [
        {"from": lo + i * width, "to": lo + (i + 1) * width, "count": counts[i]}
        for i in range(DISTRIBUTION_BINS)
    ]


def _percentile_rank(values: list[float], latest: float) -> float:
    """最新值在窗口里的位置：`100 × count(v <= latest) / n`（`<=` 口径，无插值）。"""
    return round(100 * sum(1 for v in values if v <= latest) / len(values), 2)


def _streak(points: list[dict[str, Any]], median: float | None) -> dict[str, Any]:
    """截至最新有效日的连续同向天数（以窗口中位数为界）。

    遇到 NULL（当日算不出）或恰好等于中位数即停：那两天既不算"在上方"也不算"在下方"，
    继续累加会把中间断掉的序列读成连续。
    """
    if median is None:
        return {"side": None, "days": 0, "median": None}
    side: str | None = None
    days = 0
    for point in reversed(points):
        value = point.get("value")
        if value is None or point.get("low_sample"):
            break
        if value > median:
            current = "above"
        elif value < median:
            current = "below"
        else:
            break
        if side is None:
            side = current
        elif current != side:
            break
        days += 1
    return {"side": side, "days": days, "median": median}


def _peer_entries(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """9 个指标各自的最新有效值（同一次窗口读取，不再查库）。"""
    peers: list[dict[str, Any]] = []
    for defn in METRIC_DEFS.values():
        latest_value: float | None = None
        latest_date: date | None = None
        low_sample = False
        # 切换器展示**最新原始值**（与详情页 hero 一致）；样本不足用 `low_sample` 标记，
        # 不能悄悄退到上一个有效值——那会出现"chip 18%、页面 50%"的矛盾。
        for point in reversed(series):
            value = point.get(defn.key)
            if value is None:
                continue
            latest_value = float(value)
            latest_date = point["trade_date"]
            low_sample = _low_sample(defn, point)
            break
        peers.append(
            {
                "key": defn.key,
                "label": defn.label,
                "unit": defn.unit,
                "latest_value": latest_value,
                "latest_date": latest_date,
                "low_sample": low_sample,
            }
        )
    return peers


def metric_payload(
    rows: list[Any],
    metric_key: str,
    *,
    as_of: date | None,
    as_of_quality: str,
    days: int,
    display_days: int | None = None,
) -> dict[str, Any]:
    """组装指标详情响应（**纯函数**，无 IO —— 缓存由 :func:`get_metric_detail` 负责）。

    行序按交易日升序（repo 保证，这里再排一次以兼容缓存/替身）。
    """
    from app.services import sentiment_regime_service as regime_svc  # noqa: PLC0415
    from app.services.limit_up_service import sentiment_series_row  # noqa: PLC0415

    defn = METRIC_DEFS[metric_key]
    series_all = [sentiment_series_row(r) for r in rows]
    series_all.sort(key=lambda r: r["trade_date"])
    # 阶段分位需要 250 日上下文；展示/统计只取最后 display_days 行。
    display = series_all[-(display_days or len(series_all)) :] if series_all else []

    points: list[dict[str, Any]] = []
    values: list[float] = []
    excluded_days = 0
    for row in display:
        n = _sample_n(defn, row)
        value = _raw_value(defn, row)
        low_sample = _low_sample(defn, row)
        if value is not None and low_sample:
            excluded_days += 1
        points.append(
            {
                "trade_date": row["trade_date"],
                "value": value,
                "n": n,
                "low_sample": low_sample,
            }
        )
        if value is not None and not low_sample:
            values.append(value)

    latest = points[-1] if points else None
    # 上一个**有效**点（跨过 NULL 日）：均值/比值的「较前值」必须与同一口径比。
    previous = next(
        (p for p in reversed(points[:-1]) if p["value"] is not None and not p["low_sample"]),
        None,
    )
    delta = (
        round(latest["value"] - previous["value"], 6)
        if latest
        and latest["value"] is not None
        and not latest["low_sample"]
        and previous is not None
        else None
    )
    stats = _stats(values)
    # 连续天数用**未四舍五入**的中位数比较：`stats.median` 保留 6 位，恰好落在中位数上的
    # 值可能因舍入被误判成 above/below（比值类指标的分母常产生 1/3 这类无限小数）。
    median = statistics.median(values) if values else None
    insufficient = len(values) < MIN_PERCENTILE_SAMPLES

    latest_interval = None
    if (
        latest is not None
        and latest["value"] is not None
        and latest["n"] is not None
        and not latest["low_sample"]
        and defn.key in RATIO_METRICS
        and latest["n"] >= MIN_SAMPLE_N
    ):
        successes = int(round(latest["value"] * latest["n"]))
        latest_interval = _wilson(successes, latest["n"])

    sample_gate = None
    if defn.sample_field is not None or defn.key == "broken_rate":
        sample_gate = {
            "field": "zt_count+zb_count" if defn.key == "broken_rate" else defn.sample_field,
            "min_n": MIN_SAMPLE_N,
            "excluded_days": excluded_days,
        }

    return {
        "metric": {
            "key": defn.key,
            "label": defn.label,
            "unit": defn.unit,
            "group": defn.group,
            "hotter_when": defn.hotter_when,
            "caliber": defn.caliber,
        },
        "as_of": as_of,
        "as_of_quality": as_of_quality,
        "window": {
            "days": days,
            "from": points[0]["trade_date"] if points else None,
            "to": points[-1]["trade_date"] if points else None,
            "samples": len(points),
        },
        "latest": latest,
        "previous": previous,
        "delta": delta,
        "percentile_rank": (
            None
            if insufficient or latest is None or latest["value"] is None or latest["low_sample"]
            else _percentile_rank(values, latest["value"])
        ),
        "stats": stats,
        "distribution": _distribution(values),
        "streak": _streak(points, median),
        "series": points,
        "peers": _peer_entries(series_all),
        "insufficient_history": insufficient,
        "latest_interval": latest_interval,
        "sample_gate": sample_gate,
        "regime": regime_svc.build_regime(series_all),
    }


def metric_cache_key(metric_key: str, days: int) -> str:
    """读缓存键：改变结果集的维度只有 (指标, 窗口天数)。"""
    return f"{_METRIC_CACHE_PREFIX}:{metric_key}:{days}"


async def get_metric_detail(
    cache: CacheClient | None, metric_key: str, days: int = 250
) -> dict[str, Any]:
    """`GET /market/sentiment/metrics/{metric_key}` 的编排（读时计算 + Redis 缓存）。

    未知 `metric_key` → `KeyError`（端点转 404，不返回空图）；空窗口**不缓存**
    （与 calendar/trends 同一条：空结果不得被冻结）。
    """
    if metric_key not in METRIC_DEFS:
        raise KeyError(metric_key)

    from app.services import market_day_service  # noqa: PLC0415

    key = metric_cache_key(metric_key, days)
    if cache is not None:
        cached: dict[str, Any] | None = await cache.get(key)
        if cached:
            return cached

    db: AsyncSession
    async with async_session_factory() as db:
        as_of = await limit_up_repo.latest_sentiment_day(db)
        rows = await limit_up_repo.list_sentiment_days(db, days)
        market = await market_day_service.resolve_latest_complete_day(db)
    quality = market.quality if market is not None else "partial"

    payload = metric_payload(rows, metric_key, as_of=as_of, as_of_quality=quality, days=days)
    if cache is not None and payload["window"]["samples"]:
        await cache.set(key, payload, ttl=METRIC_TTL)
    return payload
