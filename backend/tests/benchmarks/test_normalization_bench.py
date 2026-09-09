"""CPU 基准：行情字段归一化 mapper（Tier 1 纯计算域，无 DB）。

覆盖 map_daily_rows —— 把 TuShare 原始 fund_daily/cb_daily 行（字符串日期、
vol/amount 等）归一化为落库行。合成 dict 输入，不触库。
尺寸与种子是基线契约。
"""

import random
from datetime import date, timedelta

import pytest

from app.services.securities_service import map_daily_rows

pytest.importorskip("pytest_benchmark")

# ── 基线契约：尺寸与种子（改动即失基线，须刷新 baseline.json） ──────────
_N_ROWS = 5000  # 一次归一化的原始行数；刻意小——rounds 多、median 收敛稳（大样本抖动 >7%）
_SEED = 20260909
_TS_CODE = "159865.SZ"


def _build_raw_rows(n: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    base = date(2023, 1, 3)
    rows: list[dict] = []
    for i in range(n):
        d = base + timedelta(days=i % 365)
        rows.append(
            {
                "ts_code": _TS_CODE,
                "trade_date": d.strftime("%Y%m%d"),
                "open": round(rng.uniform(10.0, 20.0), 2),
                "high": round(rng.uniform(10.0, 22.0), 2),
                "low": round(rng.uniform(9.0, 19.0), 2),
                "close": round(rng.uniform(10.0, 21.0), 2),
                "pre_close": round(rng.uniform(10.0, 21.0), 2),
                "vol": rng.randint(1_000_000, 50_000_000),
                "amount": rng.randint(100_000_000, 2_000_000_000),
            }
        )
    return rows


def _run_map(raw: list[dict]) -> int:
    return len(map_daily_rows(raw))


@pytest.mark.bench
def test_map_daily_rows_batch(benchmark) -> None:
    raw = _build_raw_rows(_N_ROWS, _SEED)
    n = benchmark(_run_map, raw)
    assert n == _N_ROWS
