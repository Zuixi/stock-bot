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

let failed = 0;
/** 统一失败记账（解析错误与"未定义变量"走同一条路径，避免两套计数各说各话）。 */
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};

function blockOf(source, marker) {
  const start = source.indexOf(marker);
  if (start === -1) throw new Error(`marker not found: ${marker}`);
  return source.slice(start, source.indexOf("}", start));
}
/** 记账版取块：块不存在 → 记一次失败并返回 null，**不中断**后续断言（见 §8.1 的"每组各自报红"）。 */
function blockOrFail(source, marker, label) {
  try {
    return blockOf(source, marker);
  } catch {
    fail(`${label}：theme.css 找不到 \`${marker}\` 块 —— 该块的全部断言本轮无法执行`);
    return null;
  }
}
/** 记账版取色：取不到 → 记一次失败并返回 null（对比度/一致性断言据此跳过，不抛异常）。 */
function colorOrFail(text, name, label) {
  try {
    return varOf(text, name);
  } catch {
    fail(`${label}：theme.css 缺少 --${name}（未定义即无法算对比度，按失败记账）`);
    return null;
  }
}
function varOf(text, name) {
  const m = text.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!m) throw new Error(`--${name} not found`);
  return m[1].toLowerCase();
}
function tsColor(mode, name) {
  const start = themeTsSource.indexOf(`${mode}: {`);
  const sec = themeTsSource.slice(start, themeTsSource.indexOf("},", start));
  // 键名允许带引号：多词令牌（如 `sent-extreme-cold`）在 TS 里只能写成字符串键。
  // 只放宽**键的引号**，色值仍是必须逐字相等的 6 位 hex —— 不放宽任何实质断言。
  const m = sec.match(new RegExp(`"?${name}"?:\\s*"(#[0-9a-fA-F]{6})"`));
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

for (const { label, marker, tsMode, surfaces } of MODES) {
  const b = blockOf(css, marker);
  // `up`/`down`（价格方向）+ `sent-extreme-cold`/`sent-extreme-hot`（/sentiment 情绪分位极值带）：
  // 同一张表里逐块校验对比度（≥4.5 on bg-page 与 bg-panel）与 theme.ts 一致性。
  // 新增颜色刻度必须进本数组，否则"看一眼觉得能读"就会成为唯一门禁。
  for (const name of ["up", "down", "sent-extreme-cold", "sent-extreme-hot"]) {
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
  let sf;
  try {
    sf = parseSource(file, raw, (message) => {
      fail(`${message} —— 该文件已从本门禁视野里消失，必须修好语法才能被校验`);
    });
  } catch (err) {
    fail(`${file} 解析失败：${err.message}`);
    continue;
  }
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

// ══════════════════════════════════════════════════════════════════════════
// §8.1（Phase 0.5）设计系统门禁五组断言
//
// 为什么要有这一段：`--mag-*`（量级色阶）/ `--font-ui` / `--font-num` /
// `prefers-reduced-motion` / `:focus-visible` 是本轮设计整改的**表达契约**。
// 它们没有任何运行时消费方会在缺失时报错——漏掉一级 `--mag-*` 只是"某些格子成了
// 继承色"，漏掉 reduced-motion 只是"动效还在转"——不测就等于没有契约。
// 故按 §8.1 五条机械断言，全部落在本文件（已在 `npm run check:design`）。
// ══════════════════════════════════════════════════════════════════════════

// ── §8.1-1/2：`--mag-0..4` 三块齐全 + 对比度 ≥4.5 + theme.ts 同名数组一致 ──
//
// 语义：`--mag-0..4` 是**数量型中性色阶**（不用 accent 编码"有多少"）。
// 数量编码必须用中性灰阶的原因见计划 §Why now：accent 叠白 alpha 后
// 0.5→2.10:1、0.85→3.81:1，都不过 AA；且 6/31 瓦片命中"白字分支"→ 颜色本身在表达数量。
// 文字**一律 --text-primary**（不再有白字分支），故对比度逐级对 --text-primary 断言。
const MAG_LEVELS = [0, 1, 2, 3, 4];
/** theme.ts 的 `mag: [...]` 数组（light/dark 各一份）——与 CSS 逐项比对，防"两套色阶各说各话" */
function tsMagArray(mode) {
  const start = themeTsSource.indexOf(`${mode}: {`);
  if (start === -1) return null;
  const sec = themeTsSource.slice(start, themeTsSource.indexOf("},", start));
  const m = sec.match(/mag:\s*\[([^\]]*)\]/);
  if (!m) return null;
  return [...m[1].matchAll(/"?(#[0-9a-fA-F]{6})"?/g)].map((x) => x[1].toLowerCase());
}
const MAG_MODES = [
  { label: "fallback", marker: ":root {", tsMode: "light" },
  { label: "light", marker: '[data-theme="light"]', tsMode: "light" },
  { label: "dark", marker: '[data-theme="dark"]', tsMode: "dark" },
];
for (const { label, marker, tsMode } of MAG_MODES) {
  const b = blockOrFail(css, marker, `§8.1-1 ${label}`);
  if (b === null) continue;
  const textPrimary = colorOrFail(b, "text-primary", `§8.1-2 ${label}`);
  const tsMag = tsMagArray(tsMode);
  if (tsMag === null) {
    fail(`§8.1-1 theme.ts(${tsMode}) 缺少 \`mag: [\` 数组 —— 量级色阶必须是同一契约的两侧`);
  } else if (tsMag.length !== MAG_LEVELS.length) {
    fail(`§8.1-1 theme.ts(${tsMode}).mag 级数 = ${tsMag.length}，契约 5 级（--mag-0..4）`);
  }
  for (const level of MAG_LEVELS) {
    const hex = colorOrFail(b, `mag-${level}`, `§8.1-1 ${label}`);
    if (hex === null) continue;
    if (tsMag && tsMag[level] !== undefined && tsMag[level] !== hex) {
      fail(`§8.1-1 ${label} mag-${level}: theme.ts(${tsMode})(${tsMag[level]}) != theme.css(${hex})`);
    }
    if (textPrimary === null) continue;
    const ratio = contrast(hex, textPrimary);
    const ok = ratio >= 4.5;
    console.log(
      `${ok ? "PASS" : "FAIL"} §8.1-2 ${label} --mag-${level} ${hex} on ${textPrimary}(text-primary): ${ratio.toFixed(2)}:1`
    );
    if (!ok) failed += 1;
  }
  if (tsMag && tsMag.length === MAG_LEVELS.length) {
    console.log(`PASS §8.1-1 ${label} --mag-0..4 齐全且与 theme.ts(${tsMode}).mag 逐项一致`);
  }
}

// ── §8.1-3：`--font-ui` / `--font-num` 存在 + `theme.ts` 的 `token.fontFamily` 引用 --font-ui ──
for (const { label, marker } of MAG_MODES) {
  const b = blockOrFail(css, marker, `§8.1-3 ${label}`);
  if (!b) continue;
  for (const name of ["font-ui", "font-num"]) {
    if (new RegExp(`--${name}\\s*:`).test(b)) console.log(`PASS §8.1-3 ${label} --${name} 已定义`);
    else fail(`§8.1-3 ${label} 缺少 --${name}（字体族/数字族分离是本轮 token 契约）`);
  }
}
if (/fontFamily:\s*[`"']?var\(--font-ui\)/.test(themeTsSource)) {
  console.log("PASS §8.1-3 theme.ts 的 token.fontFamily 引用 var(--font-ui)");
} else {
  fail("§8.1-3 theme.ts 的 token.fontFamily 未引用 var(--font-ui)（AntD 会退回默认字体族）");
}

// ── §8.1-4：`theme.css` 含 reduced-motion 全局降级 + `:focus-visible` 全局焦点环 ──
if (/@media\s*\(prefers-reduced-motion:\s*reduce\)/.test(css)) {
  console.log("PASS §8.1-4 theme.css 定义 @media (prefers-reduced-motion: reduce) 全局降级");
} else {
  fail("§8.1-4 theme.css 缺少 @media (prefers-reduced-motion: reduce) —— 无限 shimmer / hover 上浮不降级");
}
if (/:focus-visible\s*\{/.test(stripCssComments(css))) {
  console.log("PASS §8.1-4 theme.css 定义全局 :focus-visible 焦点环");
} else {
  fail("§8.1-4 theme.css 缺少全局 :focus-visible 焦点环（键盘可达性）");
}

// ── §8.1-5：自绘 `fontVariantNumeric` / `font-variant-numeric` ≤1（只允许 `.num` 定义处）──
const NUM_DECL = /fontVariantNumeric|font-variant-numeric/;
const numDecls = [];
for (const file of sourceFiles) {
  const rel = file.slice(root.length + 1);
  const raw = readFileSync(file, "utf8");
  for (const [idx, line] of raw.split("\n").entries()) {
    if (NUM_DECL.test(line)) numDecls.push(`${rel}:${idx + 1}`);
  }
}
if (numDecls.length <= 1) {
  console.log(`PASS §8.1-5 自绘等宽数字声明 = ${numDecls.length} 处（契约 ≤1，只允许 .num 定义处）`);
} else {
  fail(
    `§8.1-5 自绘 fontVariantNumeric/font-variant-numeric = ${numDecls.length} 处，契约 ≤1` +
      `（应统一走 \`.num\`；违规处：${numDecls.slice(0, 8).join("、")}${numDecls.length > 8 ? " …" : ""}）`
  );
}

if (failed) {
  console.log(`\n设计令牌门禁未通过：${failed} 项不达标`);
}
process.exit(failed ? 1 : 0);
