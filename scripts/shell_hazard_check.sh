#!/usr/bin/env bash
# ============================================================
# shell 写法陷阱检查（只查 scripts/*.sh）
# 每条规则都来自本仓库真实踩过的坑，不是泛化 lint：
#   1. `set -o pipefail` + 管道末尾 `grep -q` → grep 命中即退出，写端吃 SIGPIPE(141)，
#      整条管道判失败：**大输入时必然静默失效**（实测 308KB diff 下 slop_scan 8 个
#      分类全不命中，小输入因写入先完成而"看起来正常"）。
#   2. `declare -A`：macOS 自带 bash 3.2 不支持关联数组（实测报 invalid option）。
#   3. `timeout`：GNU coreutils 专有，macOS 无（实测 command not found）——
#      需要超时就用 `ssh -o ConnectTimeout`、`curl --max-time` 之类的原生参数。
#   4. `cat -A`：BSD cat 无该选项（实测 illegal option）。
# 破坏验证：在任意 scripts/*.sh 里临时加 `echo x | grep -q x` 或 `declare -A m` → 应变红。
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."

fails=0
# 用 find 而不是 ls：ls scripts/*.sh 不含隐藏文件，破坏验证时装不成探针
FILES=$(find scripts -maxdepth 1 -name '*.sh' ! -name 'shell_hazard_check.sh' | sort)
[ -z "$FILES" ] && { echo "  （无 scripts/*.sh）"; exit 0; }

check() { # $1=说明 $2=正则
  local hits
  # 跳过注释行：注释里举反例（如“不要写 printf | grep -q”）不该被判违规
  hits=$(grep -nE "$2" $FILES 2>/dev/null | grep -vE "^[^:]+:[0-9]+:[[:space:]]*#" || true)
  if [ -n "$hits" ]; then
    echo "  ✘ $1"
    printf '%s\n' "$hits" | sed 's/^/      /'
    fails=$((fails + 1))
  fi
}

echo "== shell 陷阱检查 =="
check "管道末尾用 grep -q（pipefail 下会因 SIGPIPE 静默失效）→ 改用 here-string: grep -qE \"\$pat\" <<< \"\$VAR\"" '\|[[:space:]]*grep[[:space:]]+-[a-z]*q'
check "declare -A（macOS bash 3.2 不支持关联数组）→ 改用 case 或 两个平行数组" 'declare[[:space:]]+-A'
check "timeout（GNU 专有，macOS 无）→ 用原生命令的超时参数" '(^|[^a-zA-Z_])timeout[[:space:]]'
check "cat -A（BSD cat 不支持）→ 用 sed -n l 或 python" 'cat[[:space:]]+-A'

if [ "$fails" -eq 0 ]; then
  echo "  ✔ 未发现已知 shell 陷阱（4 项）"
  exit 0
else
  echo "✘ shell 陷阱检查失败：$fails 项"
  exit 1
fi
