#!/usr/bin/env bash
# ============================================================
# CPU 基准套件（Tier 1 硬门禁）— pytest-benchmark + 相对基线
# 见 plans/2026-09-09-benchmark-tiers.md；被 CI bench-cpu job 调用。
#
# 基线口径：wall-clock 基线绑定硬件 —— 入库 benchmarks/baseline.json 仅作
# 本机开发参考；CI 门禁用**同 runner A/B**（先跑 PR base commit 存临时基线，
# 再跑 head 对比），见 ci.yml bench-cpu。
#
# 用法（仓库根目录）：
#   bash scripts/bench.sh                    # 跑基准 + 与基线对比门禁（CI/本地同款）
#   bash scripts/bench.sh --save-baseline    # 刷新 benchmarks/baseline.json（基线契约变更时）
#   bash scripts/bench.sh --quick            # 本地冒烟：少 rounds，不门禁
#   bash scripts/bench.sh --allow-added      # gate 时新增基准不判失败（CI A/B 传此参）
#
# 环境变量：
#   BENCH_THRESHOLD  相对退化阈值（默认 0.12；Phase B 换指令数测量后建议收紧 0.05）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
# CI A/B 模式用 BENCH_BASELINE/BENCH_CURRENT 指到 $RUNNER_TEMP，避免污染
# working tree 里 tracked 的 baseline.json（否则 git checkout 会拒切）
BASELINE="${BENCH_BASELINE:-$ROOT/benchmarks/baseline.json}"
CURRENT="${BENCH_CURRENT:-$ROOT/benchmarks/last.json}"
THRESHOLD="${BENCH_THRESHOLD:-0.12}"

MODE=gate
ALLOW_ADDED=0
for arg in "$@"; do
  case "$arg" in
    --save-baseline) MODE=save ;;
    --quick) MODE=quick ;;
    --allow-added) ALLOW_ADDED=1 ;;
  esac
done

# base commit 尚无基准套件时（首次引入基准的 PR 的 A/B 侧）短路：不跑、
# 写空产物，让后续对比走"全部新增"分支而非报错
if [ ! -d "$ROOT/backend/tests/benchmarks" ]; then
  echo "== tests/benchmarks 不存在（该 commit 无基准套件），跳过 =="
  mkdir -p "$ROOT/benchmarks"
  echo '{"benchmarks": [], "created_at": "", "commit_count": 0}' > "$CURRENT"
  if [ "$MODE" = save ]; then cp "$CURRENT" "$BASELINE"; fi
  exit 0
fi

PYTEST_ARGS=(tests/benchmarks -m bench --no-cov --benchmark-disable-gc --benchmark-warmup=on -q)
if [ "$MODE" = quick ]; then PYTEST_ARGS+=(--benchmark-min-rounds=3); fi

echo "== CPU 基准套件（mode=$MODE, threshold=$THRESHOLD）=="
# 注意：pyproject addopts 默认 -m 'not e2e and not bench'，此处命令行 -m bench 覆盖之
(cd backend && uv run pytest "${PYTEST_ARGS[@]}" --benchmark-json="$CURRENT")

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

EXTRA_ARGS=()
[ "$ALLOW_ADDED" = 1 ] && EXTRA_ARGS+=(--allow-added)
# bench_compare.py 经解释器调用而非直接执行：Windows 上创建的文件无执行位，
# Linux CI checkout 出来直接跑会 Permission denied (exit 126)
(cd backend && uv run python "$ROOT/scripts/bench_compare.py" "$BASELINE" "$CURRENT" --threshold "$THRESHOLD" "${EXTRA_ARGS[@]}")
