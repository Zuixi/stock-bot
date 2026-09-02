"""Deterministic mock series for the pig industry workbench (dev/demo).

形状与 docs/design/prototype-pig-dashboard.html 原型一致（2024 高点 → 2026 磨底），
 seeded 随机抖动保证同日重跑结果一致。真实数据源（AKShare/协会）就绪后，
仅替换 fetcher，本模块保留为演示与联调用途。
"""

from __future__ import annotations

import calendar
import random
from datetime import date, timedelta
from typing import Any

from app.services import industry_registry as reg

# 37 个月度序列（旧 → 新），形状对齐原型
_PRICE = [16.8, 16.5, 15.8, 15.2, 14.9,
          14.6, 14.3, 14.2, 14.6, 15.4, 16.9, 18.4, 19.9, 21.1, 21.6, 21.2, 20.3,
          19.1, 17.9, 16.6, 15.6, 15.1, 15.5, 16.6, 17.3, 16.4, 15.3, 14.4, 13.8,
          13.2, 12.9, 12.5, 12.3, 12.6, 13.1, 13.5, 13.85]
_COST = [16.3, 16.2, 16.1, 16.0, 15.9,
         15.9, 15.8, 15.7, 15.6, 15.5, 15.3, 15.1, 15.0, 14.9, 14.8, 14.7, 14.6,
         14.5, 14.4, 14.3, 14.2, 14.2, 14.1, 14.1, 14.0, 14.0, 13.9, 13.9, 13.8,
         13.8, 13.7, 13.7, 13.6, 13.6, 13.5, 13.5, 13.4]
_CORN = [2.42, 2.44, 2.46, 2.45, 2.43,
         2.38, 2.35, 2.33, 2.32, 2.34, 2.38, 2.42, 2.45, 2.48, 2.50, 2.51, 2.52,
         2.53, 2.54, 2.55, 2.56, 2.57, 2.58, 2.59, 2.60, 2.60, 2.61, 2.62, 2.63,
         2.46, 2.47, 2.48, 2.50, 2.52, 2.50, 2.47, 2.43]
_SOW = [4220, 4205, 4190, 4180, 4160,
        4140, 4120, 4100, 4080, 4060, 4045, 4030, 4020, 4005, 3990, 3980, 3975,
        3970, 3968, 3972, 3980, 3995, 4010, 4030, 4045, 4055, 4060, 4055, 4048,
        4040, 4030, 4018, 4005, 3992, 3988, 3984, 3982]

_MONTHS = len(_PRICE)


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _monthly_periods(today: date, count: int) -> list[date]:
    """Last ``count`` month-end dates ending with the current month."""
    periods: list[date] = []
    year, month = today.year, today.month
    for _ in range(count):
        periods.append(_month_end(year, month))
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(periods))


def _wobble_series(base: list[float], rng: random.Random, scale: float, n: int) -> list[float]:
    """n 个点：前 n-1 个带抖动，末点精确等于基准值（保证日/月口径一致）。"""
    out = [round(b * (1 + rng.gauss(0, scale)), 3) for b in base[: n - 1]]
    return out + [round(base[-1], 3)]


def build_pig_mock_points(industry_key: str = "pig", months: int = _MONTHS) -> list[dict[str, Any]]:
    """Build all mock metric rows (industry-level) for upsert.

    ``months`` 截取月度历史窗口（取各序列最后 N 个月）；日度/周度/年度段不受影响。
    """
    months = max(1, min(months, _MONTHS))  # 越界钳制而非报错：API 上限 120 > 序列长度 37
    cfg = reg.PIG_INDUSTRY
    today = date.today()
    rng = random.Random(42)  # noqa: S311 - deterministic demo data
    rows: list[dict[str, Any]] = []

    def add(metric_key: str, freq: str, period: date, value: float | None) -> None:
        m = cfg.metric(metric_key)
        if m is None or value is None:
            return
        rows.append({
            "industry_key": industry_key,
            "stock_id": 0,
            "metric_key": metric_key,
            "source": "mock",
            "source_tier": m.tier,
            "freq": freq,
            "period": period,
            "value": value,
            "unit": m.unit or None,
            "extra": None,
        })

    periods = _monthly_periods(today, months)

    # ── 月度历史 ──
    for period, price, cost, corn, sow in zip(
        periods, _PRICE[-months:], _COST[-months:], _CORN[-months:], _SOW[-months:],
        strict=True,
    ):
        add("hog_price", "monthly", period, price)
        add("industry_cost_avg", "monthly", period, cost)
        add("corn_price", "monthly", period, corn)
        add("sow_inventory", "monthly", period, float(sow))

    # ── 日度序列（近 45 天，末点精确等于月度最新值） ──
    daily_days = 45
    daily_specs = [
        ("hog_price", _PRICE[-1], 2),
        ("corn_price", _CORN[-1], 3),
        ("soybean_meal_price", 3.08, 3),
        ("pork_wholesale", 19.6, 2),
        ("lh_future_main", 14850.0, 0),
    ]
    for metric_key, base, digits in daily_specs:
        series = _wobble_series([base] * daily_days, rng, 0.004, daily_days)
        for i in range(daily_days):
            period = today - timedelta(days=daily_days - 1 - i)
            add(metric_key, "daily", period, round(series[i], digits))

    # ── 周度：仔猪价格（近 26 周，缓降） ──
    for week in range(26):
        period = today - timedelta(days=7 * (25 - week))
        add("piglet_price_15kg", "weekly", period, round(30.2 - 0.07 * week, 2))

    # ── 年度：效率指标 ──
    for year, msy, psy, fcr in [(2024, 2.05, 23.2, 2.75), (2025, 2.06, 23.4, 2.73)]:
        yperiod = date(year, 12, 31)
        add("msy", "yearly", yperiod, msy)
        add("psy", "yearly", yperiod, psy)
        add("feed_meat_ratio", "yearly", yperiod, fcr)
    add("msy", "yearly", today, 2.08)
    add("psy", "yearly", today, 23.6)
    add("feed_meat_ratio", "yearly", today, 2.72)

    return rows


# ── 通用 mock builder（泛化验证，P6） ─────────────────────────────────
# 选择通用（配置驱动）而非 broiler 专用小 builder：新演示行业 = registry 里给
# MetricDef 配 mock_base 即出数，不再每行业写一个 builder 模块；pig 的原型
# 对齐序列继续走专用 builder（形状语义不可由单基准值表达）。

_WOBBLE_SCALE = 0.004


def build_generic_mock_points(
    cfg: reg.IndustryConfig,
    months: int = 12,
    *,
    days: int = 45,
    weeks: int = 26,
) -> list[dict[str, Any]]:
    """对 cfg 中所有配置了 ``mock_base`` 的指标，按注册频率生成抖动序列。

    - daily → 近 ``days`` 天逐日；weekly → 近 ``weeks`` 周每周；其余频率（monthly/
      quarterly/yearly）→ 近 ``months`` 个月末对齐，行 freq 用注册频率标注；
    - 末点精确等于 ``mock_base``（与 pig builder 口径一致），seeded 抖动可重跑复现。
    """
    rng = random.Random(42)  # noqa: S311 - deterministic demo data
    today = date.today()
    rows: list[dict[str, Any]] = []

    for m in cfg.metrics:
        if m.mock_base is None:
            continue
        if m.freq == "daily":
            periods = [today - timedelta(days=days - 1 - i) for i in range(days)]
        elif m.freq == "weekly":
            periods = [today - timedelta(weeks=weeks - 1 - i) for i in range(weeks)]
        else:
            periods = _monthly_periods(today, max(1, months))
        series = _wobble_series([m.mock_base] * len(periods), rng, _WOBBLE_SCALE, len(periods))
        for period, value in zip(periods, series, strict=True):
            rows.append({
                "industry_key": cfg.key,
                "stock_id": 0,
                "metric_key": m.key,
                "source": "mock",
                "source_tier": m.tier,
                "freq": m.freq,
                "period": period,
                "value": value,
                "unit": m.unit or None,
                "extra": None,
            })

    return rows


def build_industry_mock_points(cfg: reg.IndustryConfig, months: int = 37) -> list[dict[str, Any]]:
    """行业 mock 统一入口：pig 走原型对齐专用序列，其余行业走 mock_base 通用抖动。"""
    if cfg.key == "pig":
        return build_pig_mock_points(cfg.key, months=months)
    return build_generic_mock_points(cfg, months=months)
