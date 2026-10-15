#!/usr/bin/env bash
#
# 自托管字体子集生成（计划 §3.2 / §0-C 裁定的 **C1** 路径）—— **仅构建期**，不进运行时。
#
# ⚠️ 现状：**C1 已实测判定超预算，线上按 C2 交付**（中文走系统栈、数字走 ui-monospace，
#    零字体字节）。本脚本**不是**构建流程的一部分、**不**被任何 npm script 调用；
#    它入库的目的是让 C1 的路径可复现、可在 owner 放宽预算后一键切换（见文件尾「如何切到 C1」）。
#
# 实测（2026-10-15，本仓库 frontend 源码字符集；见 task-25 报告 §3.2）：
#   • Noto Sans SC 子集（1168 个 CJK 字形 + ASCII + 标点，woff2）：
#       400 → 194,532 B   500 → 197,008 B   600 → 197,444 B   ⇒ 3 字重合计 588,984 B
#     预算（§0-C 的「约 120KB × 2 字重」）= 245,760 B ⇒ **超预算 2.4×**（按 2 字重算也超 1.6×）。
#   • 若按计划的「常用 3500 字」子集（3500 字形规模代理集）：
#       400 → 492,880 B   500 → 499,440 B   600 → 501,148 B   ⇒ 超预算 6×。
#   • IBM Plex Mono 数字子集（`0-9 + - % . , 亿 万`）：
#       400 → 3,300 B     600 → 4,096 B     ⇒ **在 15KB 预算内**（C1 的数字一半是可行的）。
#
# 依赖：python3 + fonttools[woff]（提供 `pyftsubset` / `fonttools`）+ brotli。两者都只在
#       **开发机/CI 的构建期**需要，不进 Docker 运行时镜像，也不进 package.json。
#
# 用法：
#   cd frontend && bash scripts/subset-fonts.sh              # 生成到 public/fonts/
#   bash scripts/subset-fonts.sh /tmp/out                    # 生成到指定目录
#
# 注意：字体源文件（Noto Sans SC VF ≈ 17.7MB、IBM Plex Mono ≈ 136KB/字重）**不入库**——
#       仓库只存子集产物（切到 C1 时）。
set -euo pipefail

OUT_DIR="${1:-$(cd "$(dirname "$0")/.." && pwd)/public/fonts}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

NOTO_VF_URL="https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/TTF/Subset/NotoSansSC-VF.ttf"
PLEX_URL="https://raw.githubusercontent.com/google/fonts/main/ofl/ibmplexmono"
NOTO_WEIGHTS=(400 500 600)
PLEX_WEIGHTS=(400 600)

echo "==> 准备工具（fontTools + brotli）"
python3 - <<'PY'
import sys
try:
    import fontTools, brotli  # noqa: F401
except ImportError:
    sys.exit("缺少 fontTools/brotli：python3 -m pip install 'fonttools[woff]' brotli")
PY
PYFTSUBSET="$(command -v pyftsubset || echo python3 -m fontTools.subset)"
INSTANCER="$(command -v fonttools || echo python3 -m fontTools)"

echo "==> 下载字体源（不入库）"
curl -sSL -o "$WORK/NotoSansSC-VF.ttf" "$NOTO_VF_URL"
curl -sSL -o "$WORK/IBMPlexMono-Regular.ttf" "$PLEX_URL/IBMPlexMono-Regular.ttf"
curl -sSL -o "$WORK/IBMPlexMono-SemiBold.ttf" "$PLEX_URL/IBMPlexMono-SemiBold.ttf"

echo "==> 取字符集：本仓库 frontend 源码里实际出现的字符（保证屏幕上不会出豆腐块）"
SRC_ROOT="$(cd "$(dirname "$0")/../src" && pwd)"
python3 - "$SRC_ROOT" "$WORK/charset.txt" <<'PY'
import os, sys
root, out = sys.argv[1], sys.argv[2]
chars = set()
for dp, _, fns in os.walk(root):
    for fn in fns:
        if fn.endswith((".ts", ".tsx", ".css", ".html")):
            with open(os.path.join(dp, fn), encoding="utf-8", errors="ignore") as fh:
                chars.update(fh.read())
p = os.path.join(root, "..", "index.html")
if os.path.exists(p):
    with open(p, encoding="utf-8", errors="ignore") as fh:
        chars.update(fh.read())
chars.update("0123456789+-%.,:;()[]{}<>/\\| \u00b7\u2014\u2018\u2019\u201c\u201d\u2026\uff0c\u3002\uff1a\uff1b\uff01\uff1f\uff08\uff09\u300a\u300b")
with open(out, "w", encoding="utf-8") as fh:
    fh.write("".join(sorted(chars)))
print(f"    字符集 {len(chars)} 个字符（其中 CJK {sum(1 for c in chars if '\u4e00' <= c <= '\u9fff')} 个）")
PY

echo "==> 实例化 Noto Sans SC 可变字体 → 静态字重"
for w in "${NOTO_WEIGHTS[@]}"; do
  $INSTANCER varLib.instancer -q -o "$WORK/noto-$w.ttf" "$WORK/NotoSansSC-VF.ttf" "wght=$w" >/dev/null
done

mkdir -p "$OUT_DIR"
echo "==> 子集化（woff2）"
for w in "${NOTO_WEIGHTS[@]}"; do
  $PYFTSUBSET "$WORK/noto-$w.ttf" --text-file="$WORK/charset.txt" --flavor=woff2 \
    --layout-features='*' --no-hinting --desubroutinize \
    --output-file="$OUT_DIR/noto-sans-sc-$w.subset.woff2" >/dev/null
done
printf '0123456789+-%%,.万亿' > "$WORK/charset-num.txt"
$PYFTSUBSET "$WORK/IBMPlexMono-Regular.ttf" --text-file="$WORK/charset-num.txt" --flavor=woff2 \
  --output-file="$OUT_DIR/ibm-plex-mono-400.subset.woff2" >/dev/null
$PYFTSUBSET "$WORK/IBMPlexMono-SemiBold.ttf" --text-file="$WORK/charset-num.txt" --flavor=woff2 \
  --output-file="$OUT_DIR/ibm-plex-mono-600.subset.woff2" >/dev/null

echo "==> 产物体积（对照 §0-C 预算：Noto 约 120KB×2 字重、Plex 约 15KB）"
ls -l "$OUT_DIR"
NOTO_TOTAL=$(stat -f%z "$OUT_DIR/noto-sans-sc-400.subset.woff2" 2>/dev/null || stat -c%s "$OUT_DIR/noto-sans-sc-400.subset.woff2")
if [ "$NOTO_TOTAL" -gt 122880 ]; then
  echo "    ❌ 单字重 Noto 子集 = ${NOTO_TOTAL} B > 预算 122,880 B —— 依 §0-C 裁定应降级 **C2**" >&2
  echo "       （C2 = 中文走系统栈、数字走 ui-monospace，零字体字节；不要留 @font-face 指向缺失文件）" >&2
  exit 2
fi
echo "    ✅ 在预算内 —— 把产物留在 frontend/public/fonts/ 并按下节加 @font-face"
cat <<'EOF'

如何切到 C1（owner 放宽预算并批准后）：
  1) 本脚本产物放入 frontend/public/fonts/；
  2) 在 src/app/styles/theme.css 的三块 :root 里把
       --font-ui: "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
     改为
       --font-ui: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif;
     （--font-num 若也自托管，则改为 "IBM Plex Mono", ui-monospace, ...）；
  3) 同文件加 @font-face（font-display: swap + font-weight 分档）指向 public/fonts/ 下的产物。
  调用点一处不动——这正是 --font-ui / --font-num 抽象层的意义。
EOF
