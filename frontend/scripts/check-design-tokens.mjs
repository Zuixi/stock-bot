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

// 注释感知的扫描原语（Ruling 45）：两个源码级门禁都不许直接在原文上跑正则。
import { findVarRefs, lineOf, stripComments } from "./lib/source-scan.mjs";

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
// 定义面 = `src/` 下所有 `.css/.ts/.tsx` 里的 `--x:` 声明（含 `theme.css`、各组件局部变量
// 如 `--ladder-h1`/`--sw3-h2`/`--thermo-up-tint`，以及 **JSX 内联样式对象**里的
// `"--x": "…"` 写法）。
// 引用面 = 同一批文件里的 `var(--x…)`。
//
// **`var(--x, <fallback>)` 的语义（Ruling 45 / 探针 C 修的就是这里）**：
// 第二参存在且非空 = 提供了兜底值 → 首参**允许未定义**，这是 fallback 的用途，门禁**不得**报错；
// 无第二参（`var(--x)`）或第二参为空（`var(--x,)`，CSS 语义下不算兜底）→ 首参**必须已定义**。
// 而 fallback 表达式内部的变量（`var(--sw-heat, var(--up))` 里的 `--up`）**自身仍是引用**，
// 它若未定义就是真的错——所以 `findVarRefs` 会把它作为独立的一条 `var(` 扫出来。
//
// ⚠️ **本门禁不做注释剥离以外的语法解析**（Ruling 45 的对称性要求）：
// - **已覆盖**：注释里的 `--x:` **不再**被当成定义（先前是个假绿）；注释里的 `var(--x)` 也
//   不再被当成引用（先前是个假红）；JSX 内联样式的 `"--x":` 现在被识别为定义。
// - **未覆盖（已知限度，宁可写明也不静默）**：`--x` 的**作用域**不做校验——本门禁只断言
//   "这个名字在 `src/` 里**某处**被声明过"，不断言"在使用它的那个选择器/组件里可见"。
//   跨越样式表边界的可见性需要真正的 CSS 级联分析（当前仓库不引第三方解析器）。
//   故本门禁的定位是"消灭**死变量**（从未被声明的名字）"，而不是"校验级联"。
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
const fallbackOptional = new Set(); // 有兜底、故允许未定义的变量名
for (const file of sourceFiles) {
  const raw = readFileSync(file, "utf8");
  // **先剥注释再匹配**（Ruling 45）：否则注释里的 `--x: #fff` 会被当成真定义（假绿）、
  // 注释里举例的 `var(--x)` 会被当成真引用（假红）。两个方向都已实测复现过。
  const text = stripComments(raw);
  const rel = file.slice(root.length + 1);
  // 定义：`--x:`（可能写成 `"--x": "…"` 的内联样式键，故允许变量名两侧有引号/空格）
  for (const m of text.matchAll(/["']?(--[a-zA-Z0-9-]+)["']?\s*:/g)) definedVars.add(m[1]);
  // 引用：用注释感知的 var() 解析（能正确跨过嵌套括号，见 source-scan.mjs）
  for (const ref of findVarRefs(text)) {
    const line = lineOf(text, ref.index);
    if (ref.hasFallback) fallbackOptional.add(ref.name);
    if (!referencedVars.has(ref.name)) referencedVars.set(ref.name, []);
    const hits = referencedVars.get(ref.name);
    if (hits.length < 3) hits.push(`${rel}:${line}`);
  }
}

const undefinedVars = [...referencedVars.entries()]
  .filter(([name]) => !definedVars.has(name) && !fallbackOptional.has(name))
  .sort(([a], [b]) => a.localeCompare(b));
// 有兜底但从未声明的变量：不算失败，但**要报出来**——它意味着兜底色在永远生效，
// 很可能是"以为定义了、其实没定义"的又一次，静默才是危险的。
const fallbackOnlyVars = [...referencedVars.keys()]
  .filter((name) => !definedVars.has(name) && fallbackOptional.has(name))
  .sort();

if (undefinedVars.length === 0) {
  console.log(
    `PASS 组件引用的 CSS 变量全部已定义（引用 ${referencedVars.size} 个 / 定义 ${definedVars.size} 个，扫描 ${sourceFiles.length} 个文件）`
  );
} else {
  for (const [name, hits] of undefinedVars) {
    console.log(
      `FAIL 未定义的 CSS 变量 var(${name}) 且**未提供兜底值** —— 该声明会在 computed-value time 失效并静默继承父级颜色：${hits.join("、")}`
    );
  }
  failed += undefinedVars.length;
}
for (const name of fallbackOnlyVars) {
  console.log(
    `  ⚠ var(${name}) 未在任何文件里声明，但调用处提供了兜底值（当前不失败）：${referencedVars.get(name).join("、")} —— 兜底色在一直生效，确认是不是漏了声明`
  );
}

if (failed) {
  console.log(`\n设计令牌门禁未通过：${failed} 项不达标`);
}
process.exit(failed ? 1 : 0);
