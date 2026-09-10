#!/usr/bin/env bash
# ============================================================
# 完成前自检门禁 (Pre-completion self-review gate)
# 被 AGENTS.md「完成前自检门禁」引用，并挂进 .pre-commit 兜底。
#
# 用法（在仓库根目录执行）：
#   bash scripts/self_review.sh            # 快检：空白/冲突 + 改动文件 ruff + 文档同步告警
#   bash scripts/self_review.sh --full     # 全量：快检 + 后端 mypy/pytest + 前端 tsc
#
# 退出码：0 = 通过；1 = 有硬性未过项（空白/ruff/mypy/test/tsc）。
# 文档同步检查为启发式告警（⚠，不计失败）；硬门禁判失败（✘）。
# 说明：--full 的 pytest 需要后端可访问本地 postgres/redis（见 docs/build.md）。
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

FULL=0
if [ "${1:-}" = "--full" ]; then FULL=1; fi

fails=0
ok()   { echo "  ✔ $*"; }
warn() { echo "  ⚠ $*"; }
fail() { echo "  ✘ $*"; fails=$((fails + 1)); }

# 采集本次改动文件（含未跟踪新文件；路径含空格的项目不适用，本项目路径无空格）
CHANGED=$(git status --porcelain --untracked-files=all 2>/dev/null)
py_files() { printf '%s\n' "$CHANGED" | awk '{print $2}' | grep -E '^backend/.*\.py$'  | sed 's#^backend/##'; }
ts_files() { printf '%s\n' "$CHANGED" | awk '{print $2}' | grep -E '^frontend/.*\.(ts|tsx)$'; }
md_files() { printf '%s\n' "$CHANGED" | awk '{print $2}' | grep -E '\.md$'; }

echo "== [1/4] 空白与冲突标记 (git diff --check) =="
git diff --check        || fail "工作区存在空白/冲突标记错误"
git diff --cached --check 2>/dev/null || fail "暂存区存在空白/冲突标记错误"
ok "diff --check 通过"

PY=$(py_files)
echo; echo "== [2/4] 后端 lint（ruff，改动文件） =="
if [ -n "$PY" ]; then
  if (cd backend && uv run ruff check $PY); then ok "ruff 通过 (${PY//$'\n'/ })"; else fail "ruff 未通过"; fi
else
  echo "  （无 backend Python 改动，跳过）"
fi

echo; echo "== [3/4] 文档同步启发式（告警不计失败） =="
MD=$(md_files)
if [ -n "$MD" ]; then
  if printf '%s\n' "$MD" | grep -q '^docs/Changelog.md$'; then
    ok "Changelog 已在改动集中"
  else
    warn "改动涉及 .md 但未触及 docs/Changelog.md —— 若属功能/文档变更请按 AGENTS 约定补记"
  fi
else
  echo "  （无文档改动）"
fi

if [ "$FULL" = 1 ]; then
  echo; echo "== [4/4] 全量门禁 (--full) =="
  [ -n "$PY" ] && { echo "  mypy app/ ..."; (cd backend && uv run mypy app)  || fail "mypy 未通过"; }
  echo "  pytest (not e2e, not bench) ..."
  (cd backend && uv run pytest -m "not e2e and not bench" -q --no-cov) || fail "后端测试未通过"
  TS=$(ts_files)
  if [ -n "$TS" ]; then
    echo "  tsc --noEmit ..."
    (cd frontend && npx tsc --noEmit) || fail "前端 tsc 未通过"
  else
    echo "  （无前端 TS 改动，跳过 tsc）"
  fi
fi

echo
if [ "$fails" -eq 0 ]; then
  echo "✔ self_review 通过 — 可结束回合"
  exit 0
else
  echo "✘ self_review 失败：${fails} 项未通过 — 先修复再收尾"
  exit 1
fi
