#!/usr/bin/env bash
# ============================================================
# 自检探测器：对 git diff 按 best-practices 映射表 grep，输出应复核的分类。
# 与 docs/references/best-practices.md「自检探测器映射表」同源。
#
# 用法（仓库根）：
#   bash scripts/slop_scan.sh
#   git diff | bash scripts/slop_scan.sh
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."

# stdin 是终端时不要 cat（会一直等 EOF，交互式跑 self_review 会挂住）——
# 此时改用工作区 diff；要扫任意 diff 就显式管道：`git diff | bash scripts/slop_scan.sh`
if [ -t 0 ]; then
  DIFF=""
else
  DIFF=$(cat)
fi
if [ -z "$DIFF" ]; then
  # 工作区 diff + **未跟踪文件内容**：`git diff HEAD` 看不到新增文件，
  # 而探测器恰恰对“只新增文件”的改动集最需要发声。
  DIFF=$(git diff HEAD 2>/dev/null; git diff --cached HEAD 2>/dev/null)
  UNTRACKED=$(git ls-files --others --exclude-standard 2>/dev/null | tr '\n' '\0' | xargs -0 cat 2>/dev/null || true)
  DIFF="$DIFF
$UNTRACKED"
fi

if [ -z "$DIFF" ]; then
  echo "（无 diff，跳过 slop_scan）"
  exit 0
fi

scan() {
  local label="$1"
  local pattern="$2"
  # ❗必须用 here-string 而不是管道：`set -o pipefail` + `grep -q` 在大输入下，
  # grep 命中后提前退出会让 printf 收到 SIGPIPE(141)，整条管道被判失败 → 永远不报命中。
  # 实测：308KB 的 diff 下所有分类都静默不命中（小输入因写入先完成而“看起来正常”）。
  if grep -qiE "$pattern" <<< "$DIFF"; then
    echo "  → $label — 复核 docs/references/best-practices.md 映射表中对应分类"
  fi
}

echo "== slop_scan（探测器映射） =="
scan "数据源与采集" "source|registry|mock|trade_cal|ZoneInfo|Asia/Shanghai|to_thread|RUN_SCHEDULER|scheduler|worker|QUEUES|_get_tushare|TUSHARE_TOKEN"
scan "数据库与性能" "DISTINCT ON|LATERAL|N\\+1|index\\(|ON CONFLICT|COALESCE|::date"
scan "Docker 与部署" "Dockerfile|dockerignore|COPY --from|resolver|target: runtime|seed|service_completed_successfully"
scan "前端" "antd|EChart|notMerge|toFixed|formatCap|unit|rowKey|CheckableTag|Segmented|Tooltip"
scan "测试与 E2E" "playwright|getByText|toContainText|toBeVisible|getByRole\\(\"radio\"\\)|strict"
scan "架构与分层" "schema|repository|_dispatch_task|QUEUES|JWT|mTLS|whitelist"
scan "指标建模与规则引擎" "metric_key|freq|period|rollup|report_version|calc_method|match"
scan "工程流程与文档" "Alembic|revision|Changelog|AGENTS|best-practices|docker-compose"
echo "（命中本身不一定是错误；须确认未重复已沉淀的坑）"
