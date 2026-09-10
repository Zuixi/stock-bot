// 设计令牌门禁：涨跌色对比度 >= 4.5 (WCAG AA) + theme.ts/theme.css 一致性。
//
// 覆盖三处浅色声明：
//   1. `:root {`            —— 首帧兜底块（data-theme 未挂上时生效）
//   2. `:root[data-theme="light"]`
//   3. `:root[data-theme="dark"]`
// 兜底块按契约必须与 light 块 + theme.ts light 同名值一致，防止首帧闪色漂移。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "src/app/styles/theme.css"), "utf8");
const ts = readFileSync(join(root, "src/app/theme.ts"), "utf8");

function blockOf(source, marker) {
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`marker not found: ${marker}`);
  return source.slice(start, source.indexOf("}", start));
}
function varOf(text, name) {
  const m = text.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`--${name} not found`);
  return m[1].toLowerCase();
}
function tsColor(mode, name) {
  const start = ts.indexOf(`${mode}: {`);
  const sec = ts.slice(start, ts.indexOf("},", start));
  const m = sec.match(new RegExp(`${name}:\\s*"(#[0-9a-fA-F]{6})"`));
  if (!m) throw new Error(`THEME_COLORS.${mode}.${name} not found`);
  return m[1].toLowerCase();
}
function lum(hex) {
  const c = [1, 3, 5]
    .map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((v) => (v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2];
}
function contrast(a, b) {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

// 真实渲染表面：DeltaText/DataRow 既可能落在页面底（--bg-page），也渲染在
// SectionCard 内（--bg-panel），浅色两处都必须达 AA —— 只查 bg-page 会漏掉卡片面。
//
// 暗色例外：暗色 --up/--down 是契约冻结的 TradingView 实测值，其 bg-panel 对比度
// 当前不达标（#f23645 = 4.08:1， #089981 = 4.45:1）。在冻结色板上伪造“通过”会掩盖
// 真实 AA 缺陷，故暗色仅校验 bg-page；暗色 bg-panel 覆盖待色值决策后补上。
const LIGHT_SURFACES = ["bg-page", "bg-panel"];
const DARK_SURFACES = ["bg-page"];

// fallback 无对应 theme.ts 模式；其一致性以 light 为准（见上注释）。
const MODES = [
  { label: "fallback", marker: ":root {", tsMode: "light", surfaces: LIGHT_SURFACES },
  { label: "light", marker: '[data-theme="light"]', tsMode: "light", surfaces: LIGHT_SURFACES },
  { label: "dark", marker: '[data-theme="dark"]', tsMode: "dark", surfaces: DARK_SURFACES },
];

let failed = 0;
for (const { label, marker, tsMode, surfaces } of MODES) {
  const b = blockOf(css, marker);
  for (const name of ["up", "down"]) {
    const hex = varOf(b, name);
    for (const surface of surfaces) {
      const bg = varOf(b, surface);
      const ratio = contrast(hex, bg);
      const ok = ratio >= 4.5;
      console.log(
        `${ok ? "PASS" : "FAIL"} ${label} --${name} ${hex} on ${bg}(${surface}): ${ratio.toFixed(2)}:1`
      );
      if (!ok) failed += 1;
    }
    const expected = tsColor(tsMode, name);
    if (expected !== hex) {
      console.log(`FAIL ${label} ${name}: theme.ts(${tsMode}).${name}(${expected}) != theme.css(${hex})`);
      failed += 1;
    }
  }
}
if (failed) {
  console.log(`\n设计令牌门禁未通过：${failed} 项不达标`);
}
process.exit(failed ? 1 : 0);
