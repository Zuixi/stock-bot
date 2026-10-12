// 「不得假 0」门禁（Ruling 44 / 45 / 46）。
//
// 为什么需要它：本仓库反复出现同一种错——**"没有数据"被渲染成 `0`**。
// `0` 在数值页上是**合法值**（平盘/点位 0），所以它比空白更危险：用户与下游都读不出
// "其实是没取到"。同一类已踩过三次：
//   ① 全球卡片改造前 `price`/`change` 曾用 `?? 0`（Task 7 段 A 改为 `null` + `price_source`）；
//   ② 详情页 `/index/<tsCode>` 的 globalCards 兜底对象写 `card.price ?? 0`（Ruling 44 修）；
//   ③ 新页 `/market/global` 的 `fmtPrice` 若写 `v ?? 0` 会同样撒谎。
//
// ── Ruling 46：`.ts/.tsx` 一侧**一律走 TypeScript 编译器 API** ────────────────
// 前一版在源码原文上跑正则，一轮内连出三个缺陷，根因是同一个决定（见 lib/ast-scan.mjs 头部）：
//   NEW-1 `index.change!.toFixed(2)` 里的 `!` 让"解引用"检测失效 → **假绿**；
//   NEW-2 fallback 按**变量名**记录，A 处的 `var(--x, #fff)` 给 B 处的 `var(--x,)` 背书 → **假绿**；
//   NEW-3 自制剥注释器不认识含引号的正则字面量（`/'/`）→ 吞文件 → 假绿 + 假红。
// 这三个不是三个 bug，是"手工 lexing"一个决定的三个后果。`typescript` 已是直接依赖
// （Vite/tsc 本来就要，**零新增依赖**），故本文件现在：
//   • 用 `BinaryExpression` + `QuestionQuestionToken`/`BarBarToken` 识别 `?? 0` / `|| 0`；
//   • 用**节点形状**判断"判定型 vs 取值型"与"是否被非空断言地解引用"——
//     `!` 与 `?.` 在 AST 里是**另一种节点**，NEW-1 结构上不可能再复现；
//   • 注释/正则/模板串是**解析器**的问题，NEW-3 整类消失。
//
// ── 判定分级（有意为之，写在此处以免下一个人以为是漏判）──────────────────────
//   • **取值型 + 展示面**（`WATCHED`）→ **失败**；
//   • **取值型 + 非展示面** → **告警**（zod/DTO 的 `volume: x ?? 0`、数值聚合、排序键）；
//   • **判定型**（`(x ?? 0) > 0` 之类，只参与判定）→ **告警**；
//   • **B 规则**（可空字段必须有空值出口）只在展示面做——那里才有 `?? null` 契约。
// 全判红会让门禁被关掉或被塞进几十条 allowlist（Ruling 45 的假阳性教训）。
// `WATCHED` 仍是白名单，故有自检：文件不存在即失败，防改名后静默失去作用面。
//
// 破坏验证（见 report）：① `index.change!.toFixed(2)` → 红；② `?? null` 改回 `?? 0` → 红；
// ③ `(pctChange ?? 0) > 0` → 仅告警、通过。

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import ts from "typescript";

import {
  dereferencedField,
  isInNonNullBranch,
  lineOf,
  parseSource,
  snippetOf,
  unwrapExpression,
  walk,
} from "./lib/ast-scan.mjs";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

/**
 * **展示面**白名单：用户直接看到行情数值的地方。这里出现"取值型 `?? 0`"即失败。
 *
 * 注意这里**不再手写"哪些字段可空"**：可空集合由 AST 从该文件自己的
 * `x ?? null` 兜底**推导**（见 B 规则）。前几版手工维护字段名清单，改个变量名
 * （`index.` → `row.`）就会静默失去作用面——属于"配置与实现两处真相"。
 */
const WATCHED = [
  {
    file: "src/pages/market-index/index.tsx",
    why: "指数/全球标的详情页（globalCards 兜底对象，Ruling 44）",
    // 字段名（不带 receiver）→ 其空值出口在哪个组件内部。**逐个登记，不接受通配**。
    // `ChangeText` 的 null 分支渲染 `--`（见 `shared/ui/ChangeText.tsx`）。
    nullSafeVia: { change: "ChangeText", changePercent: "ChangeText" },
  },
  {
    file: "src/pages/market-global/index.tsx",
    why: "全球市场子页（S1.3 段 A，卡片数值一律可空）",
  },
  {
    file: "src/features/market/components/GlobalIndexCardView.tsx",
    why: "全球指数卡片（市场首页）",
  },
];

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};
const pass = (msg) => console.log(`PASS ${msg}`);

const SRC = join(root, "src");
const walkFiles = (dir) => {
  const out = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walkFiles(full));
    else if (/\.(ts|tsx)$/.test(entry.name)) out.push(full);
  }
  return out;
};

const allFiles = walkFiles(SRC);
const watchedByFile = new Map(WATCHED.map((w) => [w.file, w]));

// 自检：白名单文件必须存在，否则门禁"看起来在跑、其实没有作用面"
const missingWatched = [];
for (const { file } of WATCHED) {
  try {
    readFileSync(join(root, file), "utf8");
  } catch {
    missingWatched.push(file);
    fail(`展示面文件不存在：${file} —— WATCHED 是白名单，改名后必须同步，否则门禁静默失效`);
  }
}

const warnings = [];
const ZERO_KINDS = new Set([ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken]);

/**
 * 某个 `?? 0` / `|| 0` 是否**只用于判定**（不产出被使用的值）。
 *
 * 结构判据（AST，不再看"下一个字符是不是运算符"）：
 *   1. 它的**父节点**是二元比较/逻辑表达式且它是左操作数 → 判定型
 *      （`(x ?? 0) > 0` 的父节点就是 `>`，`?? 0` 是左操作数）；
 *   2. 父节点是 `if` / `while` / 条件表达式的条件 → 判定型；
 *   3. 父节点是实参、属性赋值、返回、数组/对象字面量元素 → **取值型**。
 */
function isJudgementOnly(node) {
  const parent = node.parent;
  if (!parent) return false;
  if (ts.isBinaryExpression(parent) && parent.left === node) {
    const k = parent.operatorToken.kind;
    const COMPARISONS = new Set([
      ts.SyntaxKind.GreaterThanToken,
      ts.SyntaxKind.GreaterThanEqualsToken,
      ts.SyntaxKind.LessThanToken,
      ts.SyntaxKind.LessThanEqualsToken,
      ts.SyntaxKind.EqualsEqualsToken,
      ts.SyntaxKind.EqualsEqualsEqualsToken,
      ts.SyntaxKind.ExclamationEqualsToken,
      ts.SyntaxKind.ExclamationEqualsEqualsToken,
      ts.SyntaxKind.AmpersandAmpersandToken,
      ts.SyntaxKind.BarBarToken,
    ]);
    if (COMPARISONS.has(k)) return true;
  }
  if (ts.isIfStatement(parent) || ts.isWhileStatement(parent) || ts.isDoStatement(parent)) {
    return parent.expression === node;
  }
  if (ts.isConditionalExpression(parent) && parent.condition === node) return true;
  if (ts.isPrefixUnaryExpression(parent) && parent.operator === ts.SyntaxKind.ExclamationToken) {
    return true;
  }
  return false;
}

/** 找到 `?? 0` 的**完整二元表达式**节点（`x ?? 0` 本身）。 */
function isZeroCoalesce(node) {
  return (
    ts.isBinaryExpression(node) &&
    ZERO_KINDS.has(node.operatorToken.kind) &&
    ts.isNumericLiteral(node.right) &&
    Number(node.right.text) === 0
  );
}

const perFile = new Map(); // 文件名 → { sf, text, zeroSites, nullFields, derefs }

for (const file of allFiles) {
  const rel = file.slice(root.length + 1);
  const text = readFileSync(file, "utf8");
  const sf = parseSource(file, text);
  const zeroSites = [];
  walk(sf, (node) => {
    if (isZeroCoalesce(node)) zeroSites.push(node);
  });
  perFile.set(rel, { sf, text, zeroSites, watched: watchedByFile.get(rel) });
}

// ── A 规则：`?? 0` / `|| 0` ────────────────────────────────────────────────
for (const [rel, info] of perFile) {
  for (const node of info.zeroSites) {
    const line = lineOf(node, info.sf);
    const code = snippetOf(node, info.sf);
    if (isJudgementOnly(node)) {
      warnings.push(`[判定型] ${rel}:${line}: ${code}`);
      continue;
    }
    if (info.watched) {
      fail(
        `展示面出现「把缺值兜成 0」的取值型写法（0 会被读成真实点位/平盘，属假数据）：` +
          `${rel}:${line}: ${code}`
      );
    } else {
      warnings.push(`[非展示面] ${rel}:${line}: ${code}`);
    }
  }
}

if (missingWatched.length === 0 && failed === 0) {
  pass(
    `展示面（${WATCHED.length} 个文件）无「把缺值兜成 0」的取值型写法；` +
      `AST 扫描 ${allFiles.length} 个 .ts/.tsx（注释/正则/字符串由解析器处理，非文本启发式）`
  );
}

// ── B 规则：可空字段必须有**空值出口**（本文件守卫，或登记的 null-safe 组件）──
//
// NEW-1 之后这条也走 AST：
//   • "本文件守卫" = 该字段的**解引用点**处在 `x == null ? … : …` / `x != null && …` 的
//     **非空分支**里（按祖先节点结构判定，不再用"文件里出现过 == null"）；
//   • "null-safe 组件" = 该字段作为 prop 传给登记的组件，**且**该字段没有别处被
//     非空断言/可选链**解引用**（`index.change!.toFixed(2)` 会在这里被抓住）。
for (const [rel, info] of perFile) {
  const { watched } = info;
  if (!watched) continue;
  const { sf } = info;

  // ① 可空字段**由 AST 推导**，两条来源：
  //    a. 完整表达式 `x.y ?? null`（就地可空）；
  //    b. **对象字面量属性** `{ value: card.price ?? null }` 的**属性名** `value`
  //       —— 详情页的兜底对象正是这种形态：字段在字面量里被兜成 null，
  //       随后通过变量（`index.value`）访问。属性名与访问名一致，故按**字段名**比对。
  //    不再手写"哪些字段可空"的清单：变量名随实现改，手写清单改名后就静默失去作用面。
  // 语义：把**流出本文件的字段名**登记为可空。两种形态：
  //   a. 对象字面量属性 `{ value: card.price ?? null }` → 登记**属性名** `value`
  //      （详情页兜底对象就是这种：字段在字面量里可空，随后以 `index.value` 访问）；
  //   b. 其他位置的 `x.y ?? null` → 登记**该访问的最后一段** `y`。
  // 登记的是**字段名**而不是整条表达式：接收者会变（`card.price` 兜底、`index.price` 访问），
  // 写成整表达式就要求两者同名，反而会在改名后静默失去作用面。
  const nullable = [];
  const addNullable = (name) => {
    if (name && !nullable.includes(name)) nullable.push(name);
  };
  walk(sf, (node) => {
    if (
      ts.isBinaryExpression(node) &&
      node.operatorToken.kind === ts.SyntaxKind.QuestionQuestionToken &&
      node.right.kind === ts.SyntaxKind.NullKeyword
    ) {
      const prop = node.parent;
      if (ts.isPropertyAssignment(prop)) {
        // (a) 属性名
        addNullable(ts.isIdentifier(prop.name) ? prop.name.text : prop.name.getText().replace(/["']/g, ""));
        return;
      }
      // (b) 访问的最后一段
      const left = unwrapExpression(node.left);
      if (ts.isPropertyAccessExpression(left)) addNullable(left.name.text);
      else addNullable(node.left.getText());
    }
  });
  if (nullable.length === 0) {
    pass(`${rel} 未使用 \`?? null\` 兜底（${watched.why}）——本项无断言面`);
    continue;
  }
  const fieldNames = nullable;

  // ② 收集解引用点：以可空字段为 receiver 再取属性/调方法。
  //    AST 判定 → `index.change!.toFixed(2)` 结构上逃不掉（NEW-1）。
  const derefs = [];
  walk(sf, (node) => {
    if (!ts.isPropertyAccessExpression(node)) return;
    const hit = dereferencedField(node, fieldNames);
    if (!hit) return;
    derefs.push({
      field: hit,
      line: lineOf(node, sf),
      code: snippetOf(node, sf),
      // ③ "空值出口" = 该解引用处在 `x == null ? … : …` / `x != null && …` 的**非空分支**里。
      //    按祖先节点结构判定；"文件里出现过 == null"（可被别的字段顶替）不再算。
      guarded: isInNonNullBranch(node, hit),
    });
  });

  const missing = [];
  for (const field of fieldNames) {
    const fieldDerefs = derefs.filter((d) => d.field === field);
    if (fieldDerefs.length === 0) {
      // 从未被解引用 → 只可能作为 prop 传给组件；须登记且组件真的被本文件使用
      // 出口必须**逐个字段接线**：该字段要真的作为 prop 传给那个组件
      // （只要求"文件里出现过 <ChangeText>"会让 change 蹭 changePercent 的组件——同一种
      //  "一处为另一处背书"，Ruling 45/46 反复否掉的就是它）
      const via = watched.nullSafeVia?.[field];
      if (via && fieldPassedToComponent(sf, via, field)) continue;
      missing.push(`${field}（没有任何空值出口：既未解引用也未登记 null-safe 组件）`);
      continue;
    }
    // 所有解引用点都必须有守卫（不是"有一个就算"）
    const unguarded = fieldDerefs.filter((d) => !d.guarded);
    if (unguarded.length === 0) continue;
    const bad = unguarded[0];
    missing.push(`${field} 在 ${bad.line} 行被无条件解引用：${bad.code}`);
  }

  if (missing.length === 0) {
    pass(
      `${rel} 的可空字段都有空值出口（本文件非空分支守卫或登记的 null-safe 组件）：` +
        `${nullable.join("、")}（${watched.why}）`
    );
  } else {
    fail(
      `${rel} 这些可空字段缺少空值出口（AST 判定；注释与"别的字段的守卫"都不算）：` +
        `${missing.join("；")} —— 视图会对 null 直接取值（${watched.why}）`
    );
  }
}

/**
 * 该字段是否**确实作为 prop** 交给了登记组件（精确标签名）。
 *
 * 精确标签名：`ChangeTextView` **不**等于 `ChangeText`（旧文本写法 `<ChangeText\b` 会误认，
 * 探针已验证）。
 * 逐字段接线：要求该字段名出现在该 JSX 元素的某个属性值里，而不是"文件里出现过这个组件"。
 */
function fieldPassedToComponent(sf, componentName, field) {
  const short = field.split(".").pop();
  let found = false;
  walk(sf, (node) => {
    if (!ts.isJsxOpeningElement(node) && !ts.isJsxSelfClosingElement(node)) return;
    if (node.tagName.getText() !== componentName) return;
    for (const attr of node.attributes.properties) {
      if (!ts.isJsxAttribute(attr) || !attr.initializer) continue;
      const text = attr.initializer.getText();
      // `value={index.change}` / `value={row.pctChange}` —— 按最后一段字段名比对
      if (new RegExp(`[.\\s{]${short}\\s*}`).test(text) || text.includes(`${short}}`)) {
        found = true;
      }
    }
  });
  return found;
}

if (warnings.length > 0) {
  console.log(
    `  ⚠ 另有 ${warnings.length} 处「折成 0 后只参与判定/算术，或落在非展示面文件」的写法，不失败：`
  );
  for (const w of warnings.slice(0, 10)) console.log(`      ${w}`);
  if (warnings.length > 10) console.log(`      …（其余 ${warnings.length - 10} 处省略）`);
}

if (failed) {
  console.log(`\n「不得假 0」门禁未通过：${failed} 项`);
}
process.exit(failed ? 1 : 0);
