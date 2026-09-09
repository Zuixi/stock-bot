#!/usr/bin/env bash
# ============================================================
# CPU 基准套件（Tier 1 硬门禁）— pytest-benchmark + 相对基线
# 见 plans/2026-09-09-benchmark-tiers.md；被 CI bench-cpu job 调用。
#
# 用法（仓库根目录）：
#   bash scripts/bench.sh                    # 跑基准 + 与基线对比门禁（CI/本地同款）
#   bash scripts/bench.sh --save-baseline    # 刷新 benchmarks/baseline.json（基线契约变更/新增基准时）
#   bash scripts/bench.sh --quick            # 本地冒烟：少 rounds，不门禁
#
# 环境变量：
#   BENCH_THRESHOLD  相对退化阈值（默认 0.12；Phase B 换指令数测量后建议收紧 0.05）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
BASELINE="$ROOT/benchmarks/baseline.json"
CURRENT="$ROOT/benchmarks/last.json"
THRESHOLD="${BENCH_THRESHOLD:-0.12}"

MODE=gate
case "${1:-}" in
  --save-baseline) MODE=save ;;
  --quick) MODE=quick ;;
esac

PYTEST_ARGS=(tests/benchmarks -m bench --no-cov --benchmark-disable-gc --benchmark-warmup=on -q)
if [ "$MODE" = quick ]; then PYTEST_ARGS+=(--benchmark-min-rounds=3); fi

echo "== CPU 基准套件（mode=$MODE, threshold=$THRESHOLD）=="
# 注意：pyproject addopts 默认 -m 'not e2e and not bench'，此处命令行 -m bench 覆盖之
(cd backend && uv run pytest "${PYTEST_ARGS[@]}" --benchmark-json=../benchmarks/last.json)

case "$MODE" in
  quick)
    echo "✔ quick 冒烟完成（未做门禁）"
    exit 0
    ;;
  save)
    mkdir -p "$ROOT/benchmarks"
    cp "$CURRENT" "$BASELINE"
    echo "✔ 基线已刷新: benchmarks/baseline.json"
    exit 0
    ;;
esac

# ── gate 模式（CI 硬门禁同款）──
if [ ! -f "$BASELINE" ]; then
  echo "✘ 缺少基线 benchmarks/baseline.json —— 先运行 bash scripts/bench.sh --save-baseline" >&2
  exit 1
fi

# bench_compare.py 经解释器调用而非直接执行：Windows 上创建的文件无执行位，
# Linux CI checkout 出来直接跑会 Permission denied (exit 126)
(cd backend && uv run python "$ROOT/scripts/bench_compare.py" "$BASELINE" "$CURRENT" --threshold "$THRESHOLD")
