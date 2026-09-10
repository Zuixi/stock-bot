"""CPU 基准：指标 rollup 与最新值仲裁（Tier 1 纯计算域，无 DB）。

覆盖日度→月度 rollup（_rollup_monthly_rows）与多频/r 源优先级裁决
（_pick_latest）。合成行直接构造 IndustryMetric ORM 对象，不触库。
尺寸与种子是基线契约。
"""

import random
from datetime import date, timedelta

import pytest

from app.models.industry_research import IndustryMetric
from app.services.industry_metric_service import _pick_latest, _rollup_monthly_rows
from app.services.industry_registry import PIG_INDUSTRY

pytest.importorskip("pytest_benchmark")

# ── 基线契约：尺寸与种子（改动即失基线，须刷新 baseline.json） ──────────
_DAILY_ROWS = 730  # 日度序列长度（≈2 年）
_SEED = 20260909


def _row(metric_key: str, source: str, period: date, freq: str, value: float) -> IndustryMetric:
    return IndustryMetric(
        industry_key="pig",
        stock_id=0,
        metric_key=metric_key,
        source=source,
        freq=freq,
        period=period,
        value=value,
    )


def _daily_series(metric_key: str, source: str, days: int, seed: int) -> list[IndustryMetric]:
    rng = random.Random(seed)
    start = date(2024, 1, 15)
    return [
        _row(
            metric_key,
            source,
            start + timedelta(days=i),
            "daily",
            round(rng.uniform(10.0, 20.0), 2),
        )
        for i in range(days)
    ]


def _run_rollup(rows: list[IndustryMetric]) -> int:
    m = PIG_INDUSTRY.metric("hog_price")
    return len(_rollup_monthly_rows(PIG_INDUSTRY, m, rows))


def _run_pick_latest(rows: list[IndustryMetric], group: dict) -> int:
    out = _pick_latest(PIG_INDUSTRY, group, "hog_price")
    return 0 if out is None else 1


@pytest.mark.bench
def test_rollup_monthly_batch(benchmark) -> None:
    rows = _daily_series("hog_price", "akshare_soozhu", _DAILY_ROWS, _SEED)
    n = benchmark(_run_rollup, rows)
    assert n > 0


@pytest.mark.bench
def test_pick_latest_conflict_batch(benchmark) -> None:
    # 构造跨年内 daily 主行 + 月内混入 monthly 副行（frequency 冲突裁决热点）
    rows = _daily_series("hog_price", "akshare_soozhu", _DAILY_ROWS, _SEED)
    rows += [_row("hog_price", "akshare_soozhu", date(2024, 6, 30), "monthly", 14.5)]
    group = {"hog_price": rows}
    n = benchmark(_run_pick_latest, rows, group)
    assert n == 1
