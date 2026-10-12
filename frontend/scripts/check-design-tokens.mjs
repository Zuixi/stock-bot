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

import ts from "typescript";

// Ruling 46 分工：
//   • `.ts/.tsx` → **TypeScript 编译器 API**（结构与词法交给解析器；注释、字符串、正则、
//     模板串、内联样式键全部是 AST 节点，不再有"手工词法"那一类假绿/假红）；
//   • `.css` 与 **字符串里的 `var(...)`** → 文本解析（CSS 无 JS 词法歧义；`var()` 是字符串内容）。
import { parseSource, walk } from "./lib/ast-scan.mjs";
import { findVarRefs, lineOf, stripCssComments } from "./lib/source-scan.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const css = readFileSync(join(root, "src/app/styles/theme.css"), "utf8");
const themeTsSource = readFileSync(join(root, "src/app/theme.ts"), "utf8");

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
  const start = themeTsSource.indexOf(`${mode}: {`);
  const sec = themeTsSource.slice(start, themeTsSource.indexOf("},", start));
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
// ── 死变量检查（Ruling 43 / 45 / 46）──────────────────────────────────────
//
// 为什么要有这一段：`--flat` 这类"看起来该存在"的变量被写进组件后，声明在
// computed-value time 失效、元素**静默继承**别的颜色——不报错、不闪、测试也不红。
// 同一处已栽过两次：① `#cf1322` 写死色值（暗色对比度 ≈2.8:1）；② 修①时写出 `var(--flat)`
// （`--flat` 是 CSS **类名修饰符** `.delta-flat`，真正存在的是 `--text-secondary`）。
//
// **本轮（Fix Round 4）改用 TypeScript 编译器 API 取 `.ts/.tsx` 的定义与引用**，
// 因为上一版的文本扫描在三处出了缺陷：
//   NEW-2：fallback 按**变量名**记录 → A 处 `var(--x, #fff)` 给 B 处 `var(--x,)` **背书**（假绿）；
//   NEW-3：自制词法器不认识含引号的正则字面量（`/'/`）→ 吞掉文件余部（假绿 + 假红）。
// 现在：**每个 `var(...)` 引用各自判定自己的 fallback**；字符串/注释/正则由解析器处理。
//
// **限度（写明而不静默）**：`--x` 的**作用域/级联**不做校验——本门禁只断言"这个名字在
// `src/` 里**某处**被声明过"，不断言"在使用它的那个选择器/组件里可见"。跨样式表边界的
// 可见性需要真正的 CSS 级联分析（当前不引第三方解析器）。故本门禁的定位是
// "消灭**死变量**（从未被声明的名字）"，而不是"校验级联"。
//
// **`var(--x, <fallback>)` 的语义（逐引用）**：
//   有非空第二参 → 该处首参**允许未定义**（这正是 fallback 的用途），不失败，但报 ⚠；
//   无第二参（`var(--x)`）或空第二参（`var(--x,)`，CSS 语义下不算兜底）→ 首参**必须已定义**。
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
/** 逐引用记录：变量名 → [{ rel, line, hasFallback }]（不合并成"名字级"事实） */
const refs = [];
for (const file of sourceFiles) {
  const rel = file.slice(root.length + 1);
  const raw = readFileSync(file, "utf8");

  if (file.endsWith(".css")) {
    // CSS：剥块注释后取 `--x: value` 定义与 `var(...)` 引用（文本解析的**限度**见文件头）
    const text = stripCssComments(raw);
    for (const m of text.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) definedVars.add(m[1]);
    for (const ref of findVarRefs(text)) {
      refs.push({ name: ref.name, hasFallback: ref.hasFallback, rel, line: lineOf(text, ref.index) });
    }
    continue;
  }

  // TS/TSX：AST。
  //   定义 = 名字以 `--` 开头的**字符串字面量**（内联样式对象 `{"--x": "1px"}`、
  //          CSS-in-JS 模板串里的 `--x:` 都由此覆盖；注释里的不算——注释根本不是节点）。
  //   引用 = 出现在**字符串字面量**里的 `var(...)`（`style={{ color: "var(--up)" }}`、
  //          模板串整段 CSS）。字符串内容是文本，故按文本解析 `var()`，但**只在 AST
  //          给出的那一个字符串值上解析**，不接触文件全文 → NEW-3 无法复现。
  const sf = parseSource(file, raw);
  walk(sf, (node) => {
    if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) {
      const value = node.text;
      if (/^--[a-zA-Z0-9-]+$/.test(value)) definedVars.add(value);
      if (value.includes("var(")) {
        for (const ref of findVarRefs(value)) {
          refs.push({
            name: ref.name,
            hasFallback: ref.hasFallback,
            rel,
            line: lineOf(raw, node.getStart(sf)),
          });
        }
      }
      // 模板串里成段的 CSS 声明（`--x: #fff;`）也认
      for (const m of value.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) definedVars.add(m[1]);
      return;
    }
    if (ts.isTemplateExpression(node)) {
      for (const span of [node.head, ...node.templateSpans.map((s) => s.literal)]) {
        const value = span.text;
        for (const m of value.matchAll(/(--[a-zA-Z0-9-]+)\s*:/g)) definedVars.add(m[1]);
        for (const ref of findVarRefs(value)) {
          refs.push({
            name: ref.name,
            hasFallback: ref.hasFallback,
            rel,
            line: lineOf(raw, span.getStart(sf)),
          });
        }
      }
    }
  });
}

// **逐引用**判定：一处有兜底只豁免它自己（NEW-2）
const undefinedRefs = refs.filter((r) => !definedVars.has(r.name) && !r.hasFallback);
const fallbackOnlyRefs = refs.filter((r) => !definedVars.has(r.name) && r.hasFallback);

if (undefinedRefs.length === 0) {
  console.log(
    `PASS 组件引用的 CSS 变量全部已定义（引用 ${new Set(refs.map((r) => r.name)).size} 个名字 / ${refs.length} 处；` +
      `定义 ${definedVars.size} 个；扫描 ${sourceFiles.length} 个文件 —— .ts/.tsx 走 AST，.css 走文本）`
  );
} else {
  for (const r of undefinedRefs) {
    console.log(
      `FAIL 未定义的 CSS 变量 var(${r.name}) 且**该处未提供兜底值** —— 声明会在 computed-value time 失效并静默继承父级颜色：${r.rel}:${r.line}`
    );
  }
  failed += undefinedRefs.length;
}
if (fallbackOnlyRefs.length > 0) {
  console.log(
    `  ⚠ ${fallbackOnlyRefs.length} 处引用的变量未在任何文件里声明，但**该处**提供了兜底值（不失败）：`
  );
  for (const r of fallbackOnlyRefs) console.log(`      var(${r.name}) @ ${r.rel}:${r.line}`);
}

if (failed) {
  console.log(`\n设计令牌门禁未通过：${failed} 项不达标`);
}
process.exit(failed ? 1 : 0);
