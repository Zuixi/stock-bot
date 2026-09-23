#!/usr/bin/env bash
# ============================================================
# 文档系统门禁 (Documentation gate)
# 被 scripts/self_review.sh 的 [3/4] 段调用，也可单独执行。
#
# 覆盖能力（每项都必须在注释里写明「故意破坏验证」——否则 checker 自己会腐烂）：
#   1  已入库 ADR 只增不改        —— 破坏：改一条 ADR 正文任意一行 → 应报错
#                                    （CI 用 DOC_GATE_ADR_BASE=<base-sha>，否则 checkout 后恒为空）
#   2  ADR 头部字段与编号连续      —— 破坏：删掉某 ADR 的 date: 行 / 把编号改成 0009
#   3  features.md 可执行契约      —— 破坏：把某条入口改成 frontend/src/pages/not-exist
#   4  门禁矩阵 ↔ CI job 单向一致  —— 破坏：在 ci.yml 里加一个 job 名不在矩阵中
#   5  端口权威 ↔ compose 实际映射 —— 破坏：改 compose 端口映射
#   6  plans 头部契约（新计划）     —— 破坏：新建 plans/2026-10-01-x.md 不带头部
#   7  文档引用的脚本必须存在       —— 破坏：在 docs/ 里引用 scripts/not-exist.sh
#   8  当前态口径一致              —— 破坏：在 mq.py 的 QUEUES 加一个未写进架构文档的 key
#   9  e2e 术语歧义（告警）        —— 破坏：在某篇 docs 里裸用 e2e
#  10  文档目录不得有空文件         —— 破坏：`touch docs/empty.md`
#  11  前端架构权威路由             —— 破坏：index「先读」列仍含 frontend-architecture.md
#
# 退出码：0 = 通过（含仅有告警）；1 = 有硬性未过项。
# 只覆盖 docs/** 与 plans/**，不检查源码（源码门禁在 self_review 的其它段）。
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

fails=0
WARNS=0
ok()   { echo "  ✔ $*"; }
warn() { echo "  ⚠ $*"; WARNS=$((WARNS + 1)); }
fail() {
  echo "  ✘ $1"
  [ -n "${2:-}" ] && echo "      hint: $2"
  fails=$((fails + 1))
}

DECISIONS_DIR="docs/decisions"
DEPLOY_DOC="docs/deployment/index.md"
TESTING_DOC="docs/testing/index.md"
FEATURES_DOC="docs/features.md"

# ADR 只增不改的比对基准：默认 HEAD（本地：工作区 vs HEAD）。
# CI 必须传 PR base（checkout 后工作区==HEAD，用 HEAD 比会永远为空 → 检查形同虚设）：
#   DOC_GATE_ADR_BASE=<base-sha> bash scripts/doc_gate.sh
ADR_BASE="${DOC_GATE_ADR_BASE:-HEAD}"
# 基准必须可解析：空值/乱值是“静默变 no-op”的典型入口（实测 CI 里曾因 outputs 写错而恒为空）
if ! git rev-parse --verify -q "$ADR_BASE^{commit}" >/dev/null 2>&1; then
  fail "ADR 比对基准无法解析：DOC_GATE_ADR_BASE='${DOC_GATE_ADR_BASE:-}'（洩传 base sha）"
  ADR_BASE="HEAD"
fi

# ── 1. 已入库 ADR 只增不改 ──────────────────────────────────
# 允许的改动：仅 status: / superseded-by: 两行。其它任何正文改动都视为篡改历史。
echo; echo "== [1/11] ADR 只增不改 =="
if [ -d "$DECISIONS_DIR" ]; then
  adr_changed=0
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    # 未入库（新文件）或首次提交的 ADR 不参与比对
    git cat-file -e "$ADR_BASE:$f" 2>/dev/null || continue
    bad=$(git diff -U0 "$ADR_BASE" -- "$f" | grep -E '^[+-]' | grep -vE '^(\+\+\+|---)' \
          | grep -vE '^[+-](status|superseded-by):' || true)
    if [ -n "$bad" ]; then
      fail "$f 正文被修改（ADR 只增不改；结论变化请新写一篇并用 supersedes 指向旧篇）" \
        "写新 ADR + 更新 docs/decisions/index.md；见 docs/index.md「变更写哪里」"
      printf '%s\n' "$bad" | head -5
      adr_changed=1
    fi
  done < <(find "$DECISIONS_DIR" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.md' | sort)
  [ "$adr_changed" = 0 ] && ok "无已入库 ADR 正文被修改（基准 ${ADR_BASE:0:8}，未入库的新 ADR 跳过）"
else
  warn "无 $DECISIONS_DIR/，跳过"
fi

# ── 2. ADR 头部字段 / 编号连续 / superseded 双向引用 ────────
echo; echo "== [2/11] ADR 头部与编号 =="
if [ -d "$DECISIONS_DIR" ]; then
  expect=1
  head_bad=0
  for f in $(find "$DECISIONS_DIR" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.md' | sort); do
    num=$(basename "$f" | cut -c1-4 | sed 's/^0*//')
    [ -z "$num" ] && num=0
    if [ "$num" != "$expect" ]; then
      fail "ADR 编号不连续：期望 $(printf '%04d' "$expect")，实际 $f"
      head_bad=1
    fi
    expect=$((num + 1))
    for key in status date supersedes superseded-by; do
      grep -qE "^$key:" "$f" || { fail "$f 缺头部字段 \`$key:\`"; head_bad=1; }
    done
  done
  # superseded → superseded-by 双向引用
  for f in $(find "$DECISIONS_DIR" -maxdepth 1 -name '[0-9][0-9][0-9][0-9]-*.md' | sort); do
    if grep -qE '^status: *superseded' "$f"; then
      target=$(grep -oE '^superseded-by: *[0-9]{4}' "$f" | grep -oE '[0-9]{4}' || true)
      if [ -z "$target" ]; then
        fail "$f 状态为 superseded 但 superseded-by 为空"
        head_bad=1
      else
        tfile=$(find "$DECISIONS_DIR" -maxdepth 1 -name "$target-*.md" | head -1)
        if [ -n "$tfile" ] && ! grep -qE "^supersedes: *$target" "$tfile"; then
          fail "$(basename "$f") 与 $target 未双向引用（$tfile 的 supersedes 未指向它）"
          head_bad=1
        fi
      fi
    fi
  done
  [ "$head_bad" = 0 ] && ok "头部字段齐全、编号从 0001 连续、superseded 双向引用正确"
fi

# ── 3. features.md 可执行契约 ───────────────────────────────
# 规则：反引号里出现的 frontend/src/{pages,features,shared}/<path> 必须存在；
#       /api/v1/<seg> 的首段必须真实存在于 app/api/v1 的 include_router 前缀；
#       出现的 frontend/src/pages/<dir> 必须被 app/router/index.tsx 引用。
echo; echo "== [3/11] features.md 契约 =="
if [ -f "$FEATURES_DOC" ]; then
  missing=0
  for p in $(grep -oE '`frontend/src/(pages|features|shared)/[A-Za-z0-9_./-]+`' "$FEATURES_DOC" | tr -d '`' | sort -u); do
    if [ -d "$p" ] || [ -f "$p" ] || [ -f "$p.ts" ] || [ -f "$p.tsx" ]; then :; else
      fail "$FEATURES_DOC 引用了不存在的路径：$p" "删功能则删 features 行；加功能则补路径与 router"
      missing=1
    fi
  done
  prefixes=$(grep -oE 'prefix="[^"]*"' backend/app/api/v1/__init__.py | sed 's/prefix="//;s/"//;s#^/##' | cut -d/ -f1 | sort -u)
  for seg in $(grep -oE '/api/v1/[a-z0-9-]+' "$FEATURES_DOC" | sed 's#/api/v1/##' | sort -u); do
    if ! grep -qx "$seg" <<< "$prefixes"; then
      fail "$FEATURES_DOC 引用了不存在的 API 前缀：/api/v1/$seg" "核对 backend/app/api/v1/__init__.py 的 include_router"
      missing=1
    fi
  done
  for d in $(grep -oE '`frontend/src/pages/[A-Za-z0-9_-]+`' "$FEATURES_DOC" | tr -d '`' | sed 's#frontend/src/pages/##' | sort -u); do
    if ! grep -q "@/pages/$d\"" frontend/src/app/router/index.tsx; then
      fail "页面 $d 未被 app/router/index.tsx 引用（删功能请同步删 features.md 对应行）" \
        "见 frontend/src/app/router/index.tsx 与 docs/features.md"
      missing=1
    fi
  done
  [ "$missing" = 0 ] && ok "所有代码入口均存在且被路由引用"
else
  warn "无 $FEATURES_DOC，跳过"
fi

# ── 4. 门禁矩阵 ↔ CI job 单向一致 ──────────────────────────
# 规则：ci.yml 里每个 job id 必须能在 docs/testing/index.md 中找到。
# 反向不要求（矩阵会引用 self_review 步骤与 bench.sh，它们不是 CI job）。
echo; echo "== [4/11] 门禁矩阵 ↔ CI job =="
if [ -f ".github/workflows/ci.yml" ] && [ -f "$TESTING_DOC" ]; then
  miss_job=0
  for job in $(awk '/^jobs:/{f=1;next} f && /^  [a-z0-9-]+:$/{gsub(/[: ]/,"");print}' .github/workflows/ci.yml); do
    if ! grep -q "$job" "$TESTING_DOC"; then
      fail "CI job \`$job\` 未登记到 $TESTING_DOC 的门禁矩阵" \
        "改 .github/workflows/ci.yml 时同步 docs/testing/index.md"
      miss_job=1
    fi
  done
  [ "$miss_job" = 0 ] && ok "ci.yml 的 job 全部已登记"
else
  warn "缺 ci.yml 或 $TESTING_DOC，跳过"
fi

# ── 5. 端口权威表 ↔ compose 实际映射 ───────────────────────
echo; echo "== [5/11] 端口 ↔ compose =="
if [ -f docker-compose.yml ] && [ -f "$DEPLOY_DOC" ]; then
  miss_port=0
  # 映射形态如 "127.0.0.1:5433:5432" ——
  # 取**宿主机侧**端口（倒数第二段），不是容器内端口
  for port in $(grep -oE '"[0-9.:]+:[0-9]+"' docker-compose.yml | tr -d '"' \
                 | awk -F: '{print $(NF-1)}' | sort -u); do
    [ "$port" = "80" ] && continue   # 80 在权威表里以 http://localhost（:80）形式出现
    if ! grep -qE "[:（(]$port|$port" "$DEPLOY_DOC"; then
      fail "compose 映射的宿主机端口 $port 未出现在 $DEPLOY_DOC 的权威表" \
        "更新 docs/deployment/index.md 权威表与 docs/ARCHITECTURE.md"
      miss_port=1
    fi
  done
  [ "$miss_port" = 0 ] && ok "compose 宿主机端口全部已在权威表登记（80 以 http://localhost 记录）"
else
  warn "缺 docker-compose.yml 或 $DEPLOY_DOC，跳过"
fi

# ── 6. plans 头部契约（仅本规则生效日之后新建的计划） ──────
echo; echo "== [6/11] plans 头部（新计划） =="
NEW_PLAN_FROM="2026-09-23"
if [ -d plans ]; then
  plan_bad=0
  for f in plans/*.md; do
    base=$(basename "$f")
    [ "$base" = "README.md" ] && continue
    [ "$base" = "index.md" ] && continue
    # 只对本规则生效日（含）之后新建的日期前缀计划生效；无日期前缀的旧计划跳过
    [[ "$base" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}- ]] || continue
    d=${base:0:10}
    if [[ "$d" > "$NEW_PLAN_FROM" || "$d" == "$NEW_PLAN_FROM" ]]; then
      for key in status scope touches updated next-action; do
        grep -qE "^$key:" "$f" || { fail "$f 缺头部字段 \`$key:\`（见 plans/README.md）" \
          "补 5 行头并登记 plans/index.md"; plan_bad=1; }
      done
    fi
  done
  [ "$plan_bad" = 0 ] && ok "已生效日（${NEW_PLAN_FROM}）之后的新计划头部齐全"
fi

# ── 7. 文档引用的脚本必须存在（仅 docs/**） ────────────────
# 只查 docs/**：plans/** 是历史与规划文档，允许引用尚未实施的脚本。
echo; echo "== [7/11] 文档引用的脚本 =="
script_bad=0
# 引用可能是仓库根相对（scripts/x.py）或子项目相对（backend/scripts/x.py）——依次尝试多个根
for ref in $(grep -rhoE '[A-Za-z0-9_./-]*scripts/[A-Za-z0-9_.-]+\.(sh|py)' docs --include='*.md' | sort -u); do
  # 归一化：去掉可能的前缀 ../（可能是从子目录相对引用，也可能是写错的仓库根相对路径）
  norm=$(printf '%s' "$ref" | sed 's#^\(\.\./\)*##')
  [ -z "$norm" ] && norm="$ref"
  found=0
  for cand in "$norm" "backend/$norm" "frontend/$norm" "auth-service/$norm" "forward-auth/$norm"; do
    [ -f "$cand" ] && { found=1; break; }
  done
  if [ "$found" = 0 ]; then
    fail "docs/** 引用了不存在的脚本：$ref" "创建脚本或改文档路径；勿在 docs 承诺未实现能力"
    script_bad=1
  fi
done
[ "$script_bad" = 0 ] && ok "docs/** 引用的脚本全部存在"

# 文档里的 `npm run <x>` 必须在 package.json scripts 中存在（防"文档承诺了不存在的能力"：
# 实测 `npm run lint` 声明 `eslint .` 而仓库既未安装 eslint 也无配置文件，曾被写成常用命令）
npm_bad=0
for s in $(grep -rhoE 'npm run [A-Za-z0-9:_.-]+' docs --include='*.md' | awk '{print $3}' | sort -u); do
  for pkg in frontend/package.json; do
    if ! grep -qE "\"$s\":" "$pkg"; then
      fail "docs/** 引用了 $pkg 中不存在的 npm script：npm run $s" \
        "前端静态校验用 npx tsc --noEmit（ADR 0006）"
      npm_bad=1
    fi
  done
done
[ "$npm_bad" = 0 ] && ok "docs/** 引用的 npm script 均已在 package.json 声明"

# ── 8. 当前态口径一致（队列名 / 服务名） ───────────────────
echo; echo "== [8/11] 口径一致（QUEUES / compose 服务名） =="
ARCH_DOC="docs/architecture/backend/ARCHITECTURE.md"
if [ -f "$ARCH_DOC" ]; then
  q_bad=0
  for q in $(sed -n '/^QUEUES/,/^}/p' backend/app/core/mq.py | grep -oE '"[a-z_.]+":' | tr -d '":' | sort -u); do
    grep -q "\`$q\`" "$ARCH_DOC" || { fail "队列 key \`$q\` 未登记到 $ARCH_DOC 的队列注册表" \
      "登记 app/core/mq.py QUEUES 并更新架构文档；见 docs/index.md 加队列"; q_bad=1; }
  done
  [ "$q_bad" = 0 ] && ok "app/core/mq.py 的 QUEUES 全部已登记"
else
  warn "无 $ARCH_DOC，跳过队列检查"
fi
if [ -f docs/ARCHITECTURE.md ]; then
  s_bad=0
  # 只取 services: 段内的键（遇下一个顶层键即停止），否则会把 volumes 的卷名也算进来
  for svc in $(awk '/^services:/{f=1;next} f && /^[a-z]/{f=0} f && /^  [a-z0-9_-]+:$/{gsub(/[: ]/,"");print}' docker-compose.yml); do
    grep -qw "$svc" docs/ARCHITECTURE.md || { fail "compose 服务 \`$svc\` 未出现在 docs/ARCHITECTURE.md" \
      "同步 docs/ARCHITECTURE.md 与 docs/deployment/index.md"; s_bad=1; }
  done
  [ "$s_bad" = 0 ] && ok "compose 服务名全部已出现在 docs/ARCHITECTURE.md"
fi

# ── 9. e2e 术语歧义（告警，不阻断） ────────────────────────
echo "== [9/11] e2e 术语消歧（告警） =="
# 只看"当前态"文档；Changelog / design / archive / best-practices 属历史记录，不追改
E2E_SCOPE="docs/index.md docs/overview.md docs/features.md docs/evolution.md docs/ARCHITECTURE.md docs/deployment docs/testing/index.md docs/decisions docs/architecture"
ambiguous=$(grep -rniE '(^|[^A-Za-z])e2e' $E2E_SCOPE --include='*.md' \
  | grep -vE '后端|前端|Playwright|playwright|marker|spec' \
  | grep -vE 'e2e[-/`]|[-/`]e2e' \
  | grep -vE 'docs/testing/index.md|docs/decisions/0007' || true)
if [ -n "$ambiguous" ]; then
  warn "以下位置裸用 e2e 未限定前后端（应写明「后端 e2e marker」或「前端 Playwright e2e」）："
  printf '%s\n' "$ambiguous" | head -8
else
  ok "无裸用 e2e 的位置"
fi

# ── 10. 文档目录不得有空文件 ─────────────────────
# 空文件在仓库里只会积累噪声（实测：`docs/plan.md` 0 行、`docs/designs/index.md` 0 字节、
# `docs/design/webpage.md` 0 字节、`src/main.py` 0 字节）。空目录需要占位时用 .gitkeep（例外）。
echo; echo "== [10/11] 无空文件（docs/ · plans/） =="
empty_files=$(find docs plans -type f -size 0 ! -name '.gitkeep' | sort)
if [ -n "$empty_files" ]; then
  fail "以下文件为空（占位而无内容）—— 要么写内容，要么删掉：" \
    "删除空文件或写入内容；见 docs/authority.md"
  printf '%s\n' "$empty_files" | sed 's/^/    /'
else
  ok "docs/ 与 plans/ 下无空文件"
fi

# ── 11. 前端架构权威路由（入口层） ─────────────────────────
echo; echo "== [11/11] 前端架构权威路由 =="
route_bad=0
if [ -f docs/index.md ]; then
  if ! grep -q 'architecture/frontend/ARCHITECTURE.md' docs/index.md; then
    fail "docs/index.md 未指向 architecture/frontend/ARCHITECTURE.md" \
      "见 docs/authority.md 前端行"
    route_bad=1
  fi
  row=$(grep -E '\| 改前端' docs/index.md | head -1 || true)
  if [ -n "$row" ]; then
    # 不用 `printf | awk | grep -q`：pipefail 下 `grep -q` 提前退出会让上游吃到 SIGPIPE
    col3=$(printf '%s' "$row" | awk -F'|' '{print $3}')
    if [[ "$col3" == *frontend-architecture.md* ]]; then
      fail "docs/index.md「先读」列仍含 frontend-architecture.md" \
        "先读列只用 architecture/frontend/ARCHITECTURE.md"
      route_bad=1
    fi
  fi
fi
[ "$route_bad" = 0 ] && ok "改前端任务指向当前态架构文档"

echo
if [ "$fails" -eq 0 ]; then
  echo "✔ doc_gate 通过（告警 $WARNS 项）"
  exit 0
else
  echo "✘ doc_gate 失败：$fails 项未通过（告警 $WARNS 项）"
  exit 1
fi
