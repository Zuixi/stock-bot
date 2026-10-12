// 设计令牌门禁：涨跌色对比度 >= 4.5 (WCAG AA) + theme.ts/theme.css 一致性
//                  + **组件引用的 `var(--x)` 必须真的被定义**（死变量，Ruling 43）。
//
// 覆盖三处浅色声明：
//   1. `:root {`            —— 首帧兜底块（data-theme 未挂上时生效）
//   2. `:root[data-theme="light"]`
//   3. `:root[data-theme="dark"]`
// 兜底块按契约必须与 light 块 + theme.ts light 同名值一致，防止首帧闪色漂移。
import { readFileSync, readdirSync } from "node:fs";
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
// SectionCard 内（--bg-panel），因此三块（fallback/light/dark）都要对两个表面
// 断言对比度 ≥ 4.5 —— 只查 bg-page 会漏掉卡片面。门禁在所有模式上一致，无例外。
const SURFACES = ["bg-page", "bg-panel"];

// fallback 无对应 theme.ts 模式；其一致性以 light 为准（见上注释）。
const MODES = [
  { label: "fallback", marker: ":root {", tsMode: "light", surfaces: SURFACES },
  { label: "light", marker: '[data-theme="light"]', tsMode: "light", surfaces: SURFACES },
  { label: "dark", marker: '[data-theme="dark"]', tsMode: "dark", surfaces: SURFACES },
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

// ── 死变量检查（Ruling 43）──────────────────────────────────────────────────
//
// 为什么要有这一段：`--flat` 这类"看起来该存在"的变量被写进组件后，声明在
// computed-value time 失效、元素**静默继承**别的颜色——不报错、不闪、测试也不红，
// 只有人去比对 `theme.css` 才会发现。同一处已经栽过两次：
//   ① `#cf1322` 写死色值（暗色对比度 ≈2.8:1，门禁只查 token 自身 → 漏过）；
//   ② 修①时写出 `var(--flat)`（`--flat` 是 CSS **类名修饰符** `.delta--flat`，
//      不是自定义属性；真正存在的是 `--text-secondary`）。
// 因此把"**引用的每一个 `var(--x)` 都必须真的被定义**"机械化——这条完全可以机械检测。
//
// 定义面 = `src/` 下所有 `.css/.ts/.tsx` 里的 `--x:` 声明（含 `theme.css` 与各组件
// 局部变量如 `--ladder-h1`、`--sw3-h2`、`--thermo-up-tint`）。
// 引用面 = 同一批文件里的 `var(--x...)`。**带 fallback 的写法**（`var(--a, var(--b))`
// 或 `var(--a, #fff)`）里，第一个参数允许未定义（这正是 fallback 的用途），
// 故只把**第一个参数**计入引用：`--a` 是引用、`--b` 是引用，其余忽略。
// 本仓库当前存在一处该写法：`SwL3LimitUpBoard.css:136` 的 `var(--sw-heat, var(--up))`。
const SRC = join(root, "src");
const walkFiles = (dir) => {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(full));
    else if (/\.(css|ts|tsx)$/.test(entry.name)) out.push(full);
  }
  return out;
};

const sourceFiles = walkFiles(SRC);
const definedVars = new Set();
const referencedVars = new Map(); // 变量名 → 出现位置（前 3 处，便于定位）
for (const file of sourceFiles) {
  const text = readFileSync(file, "utf8");
  const rel = file.slice(root.length + 1);
  // 定义：行内任意位置的 `--x:`（不锚定行首，因为可能写成 `background: var(--y); --x: #fff;`）
  for (const m of text.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) definedVars.add(m[1]);
  // 引用：var( 之后的第一个 `--x`（只取 var 调用的首参，避免把 fallback 里的参数
  // 当成"必须定义"）；`var(--x, var(--y))` 的两个参数都会各自被别的 match 抓到。
  for (const m of text.matchAll(/var\(\s*(--[a-zA-Z0-9-]+)/g)) {
    const name = m[1];
    const line = text.slice(0, m.index).split("\n").length;
    if (!referencedVars.has(name)) referencedVars.set(name, []);
    const hits = referencedVars.get(name);
    if (hits.length < 3) hits.push(`${rel}:${line}`);
  }
}

const undefinedVars = [...referencedVars.entries()]
  .filter(([name]) => !definedVars.has(name))
  .sort(([a], [b]) => a.localeCompare(b));

if (undefinedVars.length === 0) {
  console.log(
    `PASS 组件引用的 CSS 变量全部已定义（引用 ${referencedVars.size} 个 / 定义 ${definedVars.size} 个，扫描 ${sourceFiles.length} 个文件）`
  );
} else {
  for (const [name, hits] of undefinedVars) {
    console.log(
      `FAIL 未定义的 CSS 变量 var(${name}) —— 该声明会在 computed-value time 失效并静默继承父级颜色：${hits.join("、")}`
    );
  }
  failed += undefinedVars.length;
}

if (failed) {
  console.log(`\n设计令牌门禁未通过：${failed} 项不达标`);
}
process.exit(failed ? 1 : 0);
