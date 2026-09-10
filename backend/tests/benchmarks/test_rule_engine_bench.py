"""CPU 基准：猪周期规则引擎阶段判定（Tier 1 纯计算域，无 DB）。

对 evaluate_pig_cycle 跑批量合成输入，覆盖繁荣/衰退/萧条/复苏各分支。
尺寸与随机种子是基线契约 —— 改动本常量即失效基线，须随 commit 刷新
benchmarks/baseline.json。
"""

import random

import pytest

from app.services.cycle_engine import CycleInput, evaluate_pig_cycle
from app.services.industry_registry import PIG_INDUSTRY

pytest.importorskip("pytest_benchmark")

# ── 基线契约：尺寸与种子（改动即失基线，须刷新 baseline.json） ──────────
_BATCH_SIZE = 400  # 单次批量跑的样本数
_SEED = 20260909  # 固定随机种子，保证两次运行输入完全一致


def _synthetic_inputs() -> list[CycleInput]:
    rng = random.Random(_SEED)
    inputs: list[CycleInput] = []
    for _ in range(_BATCH_SIZE):
        ratio = rng.uniform(3.0, 12.0)
        inputs.append(
            CycleInput(
                ratio=ratio,
                price=rng.uniform(10.0, 25.0),
                cost=15.5,
                sow_mom_series=[rng.uniform(-2.0, 2.0) for _ in range(24)],
                ratio_series=[rng.uniform(4.0, 10.0) for _ in range(12)],
            )
        )
    return inputs


def _run_pig_cycle(inputs: list[CycleInput]) -> int:
    for inp in inputs:
        evaluate_pig_cycle(inp, PIG_INDUSTRY)
    return len(inputs)


@pytest.mark.bench
def test_evaluate_pig_cycle_batch(benchmark) -> None:
    inputs = _synthetic_inputs()
    n = benchmark(_run_pig_cycle, inputs)
    assert n == _BATCH_SIZE
