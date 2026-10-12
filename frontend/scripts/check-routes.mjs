// 路由迁移门禁（Task 5 / S1.1，永久检查）。
//
// 依据：docs/design/ia-investment-research-platform.md §4「完整路由表（既有 15 条，一条不漏）」
// 与 §4「新目标路由的建立归属」（Ruling 30 后：8 条今日不存在的 redirect 目标）。
//
// 本脚本把那张表变成**可机械核对**的清单，逐条断言：
//   1. 既有 15 条旧路由在新 router 中全部有声明（不许删）；
//   2. **8 条 redirect** 的**目标逐字一致**（含参数名，如 `/company/:symbol` 而非 `/stock/:symbol`）；
//   3. **7 条保留**路由语义未变；
//   4. 每条 redirect 的目标路由**确实存在**（否则就是把 404 从旧路径搬到新路径）；
//   5. 8 条今日不存在的目标路由全部已建立；
//   6. 未落在表内的路由不许出现（防"顺手加的"路由绕过权限口径）；
//   7. **redirect 的目标不得是「规划中」占位页**（Ruling 30：来源今天有真页面时，不许把用户送进占位页）；
//   8. **IA §4 表与净效果块声明的数字双向比对**（行数 ↔ 成员 ↔ 声明的 8/7），防文档自相矛盾。
//
// 只读 `src/app/router/index.tsx` 与 IA 文档，不 import 运行时（可在 CI 无依赖环境跑）。
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROUTER = join(root, "src/app/router/index.tsx");
const source = readFileSync(ROUTER, "utf8");

/** 从 router 源文件提取所有声明过的静态 `path="..."` */
const declaredPaths = [...source.matchAll(/path="([^"]+)"/g)].map((m) => m[1]);

// ── 契约（逐字抄自 IA 文档 §4）────────────────────────────────────────────

/** 7 条保留（语义未变，不是 redirect） */
const KEEP = [
  "/",
  "/login",
  "/market",
  "/market/industry/:level1Code",
  "/market/industry/:level1Code/:level2Code",
  // Ruling 30 退回：两条今天就有真页面的路由，不 redirect 到尚未落地的 /market/heatmap
  "/market/hot-sectors/:category",
  "/market/concept/:boardCode",
];

/** 8 条 redirect：旧路径 → 新位置（`${param ?? ""}` 已归一为 `:param`） */
const REDIRECTS = [
  { from: "/market/category", to: "/market/industry" },
  { from: "/index/:tsCode", to: "/market/index/:tsCode" },
  { from: "/stock/:symbol", to: "/company/:symbol" },
  { from: "/research", to: "/industry" },
  { from: "/research/:industryKey", to: "/industry/:industryKey" },
  { from: "/watchlist", to: "/me?tab=watchlist" },
  { from: "/tags", to: "/me?tab=tags" },
  { from: "/tags/:tagName", to: "/me?tab=tags&name=:tagName" },
];

/**
 * §4「新目标路由的建立归属」——**今日不存在的目标**（本任务必须新建）。
 * Ruling 30 后 = 8 行归属 / 11 条路由（含裸路径与带参变体、含顶栏工具目标 `/search`）。
 * `/market/heatmap` 已移出本表（它不是任何 redirect 的目标，见 PLACEHOLDER_TARGETS 处的断言）。
 */
const NEW_TARGETS = [
  "/market/industry", // 1（裸）— /market/category 的目标
  "/market/index/:tsCode", // 2
  "/company", // 3
  "/company/:symbol", // 4
  "/industry", // 5（裸）
  "/industry/:industryKey", // 6
  "/me", // 7（?tab= / ?tab=tags&name=）
  "/search", // 8（?q=，顶栏工具目标、非 redirect）
];

/** 一级导航根（IA §1 的 10 个；`/market` 属保留类，已在 KEEP 中） */
const NAV_ROOTS = [
  "/market",
  "/news",
  "/industry",
  "/company",
  "/macro",
  "/sentiment",
  "/valuegraph",
  "/strategy",
  "/risk",
  "/trade",
];

/** 允许出现的额外路由（既有页面按新前缀搬迁 / 目标带参变体）：超出即报错，防私加路由 */
const ALLOWED_EXTRA = [
  ...NAV_ROOTS,
  // 保留类带参变体与新前缀下的既有页面
  "/market/industry/:level1Code",
  "/market/industry/:level1Code/:level2Code",
  "/market/index/:tsCode",
  "/company/:symbol",
  "/industry/:industryKey",
  "/me",
  "/search",
  "/market/industry",
  // 注意：`/market/heatmap` 仍是**已声明路由**（S1.5 建真热力图），但它不是 redirect 目标
  "/market/heatmap",
];

// ── 解析 redirect 的实际目标 ──────────────────────────────────────────────

/**
 * 把 JSX 片段里的模板归一成契约写法：
 *   `/company/${symbol ?? ""}`        → `/company/:symbol`
 *   `/market/heatmap?group=${encodeURIComponent(category ?? "")}` → `/market/heatmap?group=:category`
 */
function normalizeTarget(raw) {
  return raw
    .replace(/\$\{encodeURIComponent\((\w+)\s*\?\?\s*""\)\}/g, ":$1")
    .replace(/\$\{(\w+)\s*\?\?\s*""\}/g, ":$1");
}

/** 取一段 JSX 元素（从 `<Navigate` 起算到该元素结束）的文本 */
function sliceElement(fromIndex) {
  const open = source.indexOf("<Navigate", fromIndex);
  if (open === -1) return null;
  const close = source.indexOf("/>", open);
  return { text: source.slice(open, close + 2), end: close + 2 };
}

/** 解析静态 `element={<Navigate to="X" replace />}`：路径 → 目标 */
const staticRedirects = new Map();
for (const m of source.matchAll(/<Route\s+path="([^"]+)"\s+element=\{<Navigate to="([^"]+)"[^}]*\}\s*\/>/g)) {
  staticRedirects.set(m[1], m[2]);
}

/**
 * 解析走重定向组件的路由：`<Route path="X" element={<LegacyFooRedirect />} />`
 * 再在其函数体内找到 `<Navigate to={...} />`。
 * 静态分支直接由上面的正则覆盖；这里补的是模板字符串与 `<Navigate to="字面量">`（组件内）。
 */
const componentRedirects = new Map();
const routeToComponent = new Map();
for (const m of source.matchAll(/<Route\s+path="([^"]+)"\s+element=\{<(Legacy\w+)\s*\/>\s*\}\s*\/>/g)) {
  routeToComponent.set(m[1], m[2]);
}
for (const [routePath, componentName] of routeToComponent) {
  const fnStart = source.indexOf(`function ${componentName}(`);
  if (fnStart === -1) continue;
  const element = sliceElement(fnStart);
  if (!element) continue;
  // 分支 1：`<Navigate to="字面量" .../>`
  const literal = element.text.match(/<Navigate to="([^"]+)"/);
  if (literal) {
    componentRedirects.set(routePath, literal[1]);
    continue;
  }
  // 分支 2：`<Navigate to={`模板`} .../>`
  const template = element.text.match(/<Navigate to=\{`([^`]+)`\}/);
  if (template) {
    componentRedirects.set(routePath, normalizeTarget(template[1]));
  }
}

const actualRedirects = new Map([...staticRedirects, ...componentRedirects]);

// ── 断言 ────────────────────────────────────────────────────────────────

let failed = 0;
const fail = (msg) => {
  console.log(`FAIL ${msg}`);
  failed += 1;
};
const pass = (msg) => console.log(`PASS ${msg}`);

/** 把路由模式编译成正则，用于「目标路由确实存在」的判定 */
function patternToRegex(pattern) {
  const escaped = pattern
    .split("/")
    .map((seg) => (seg.startsWith(":") ? "[^/]+" : seg.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))
    .join("/");
  return new RegExp(`^${escaped}$`);
}

/** 具体目标路径（把 `:param` 换成样例值，query 丢弃）→ 是否命中某条已声明路由 */
function targetResolves(target, declared) {
  const pathname = target.split("?")[0];
  const concrete = pathname
    .split("/")
    .map((seg) => (seg.startsWith(":") ? `x-${seg.slice(1)}` : seg))
    .join("/");
  return declared.some((p) => patternToRegex(p).test(concrete));
}

// 1 + 2：旧 15 条一条不漏，redirect 目标逐字一致
const legacy = [...KEEP, ...REDIRECTS.map((r) => r.from)];
for (const path of legacy) {
  if (declaredPaths.includes(path)) pass(`旧路由仍声明：${path}`);
  else fail(`旧路由丢失：${path}`);
}
for (const { from, to } of REDIRECTS) {
  const actual = actualRedirects.get(from);
  if (actual === undefined) fail(`redirect 未实现：${from} → ${to}`);
  else if (normalizeTarget(actual) !== to) fail(`redirect 目标不符：${from} → ${actual}（契约 ${to}）`);
  else pass(`redirect 一致：${from} → ${to}`);
}

// 4 + 5：redirect 目标可达 + 9 条目标路由已建立
for (const { from, to } of REDIRECTS) {
  if (targetResolves(to, declaredPaths)) pass(`redirect 目标可达：${to} ← ${from}`);
  else fail(`redirect 目标仍是 404：${to}（来自 ${from}）`);
}
for (const target of NEW_TARGETS) {
  if (declaredPaths.includes(target)) pass(`新目标路由已建立：${target}`);
  else fail(`新目标路由缺失：${target}`);
}

// 一级导航 10 个根全部可达
for (const root of NAV_ROOTS) {
  if (declaredPaths.includes(root)) pass(`一级导航根可达：${root}`);
  else fail(`一级导航根缺失：${root}`);
}

// 6：不许出现表外路由
const contract = new Set([...legacy, ...NEW_TARGETS, ...ALLOWED_EXTRA]);
for (const declared of declaredPaths) {
  if (contract.has(declared)) pass(`表内路由：${declared}`);
  else fail(`表外路由（未经 IA 冻结，需先改契约）：${declared}`);
}

// 严禁 `/calendar`（IA §3「明确不含 /calendar」）
if (declaredPaths.includes("/calendar")) fail("出现了被明确排除的 /calendar");
else pass("不含 /calendar");

// redirect 条数必须与契约一致（多一条少一条都要显式改契约；Ruling 30 后 = 8）
const redirectRouteCount = [...staticRedirects.keys(), ...componentRedirects.keys()].filter((p) =>
  REDIRECTS.some((r) => r.from === p)
).length;
if (redirectRouteCount === REDIRECTS.length) pass(`redirect 条数 = ${REDIRECTS.length}（不多不少）`);
else fail(`redirect 条数 = ${redirectRouteCount}，契约 ${REDIRECTS.length}`);

// 保留条数同样必须一致（Ruling 30 后 = 7）
const keepDeclared = KEEP.filter((p) => declaredPaths.includes(p)).length;
if (keepDeclared === KEEP.length) pass(`保留条数 = ${KEEP.length}（不多不少）`);
else fail(`保留条数 = ${keepDeclared}，契约 ${KEEP.length}`);

// ── Ruling 30：redirect 的目标**不得是「规划中」占位页** ──────────────────────
// 判定：某个 `path` 的 Route 元素里是否出现 PlaceholderPage；`PlaceholderPage` 经各页面的
// lazy 别名解析（`const XPage = lazy(() => import("@/pages/<dir>"))`）。
const placeholderPages = new Set();
for (const m of source.matchAll(/(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\("@\/pages\/([\w-]+)(?:\/[\w-]+)?"\)\s*\)/g)) {
  const [, constName, dir] = m;
  const pageSource = readFileSync(join(root, "src/pages", dir, "index.tsx"), "utf8");
  if (/PlaceholderPage/.test(pageSource)) placeholderPages.add(constName);
}
const placeholderRoutePaths = [];
for (const m of source.matchAll(
  /<Route\s+path="([^"]+)"\s+element=\{\s*withSuspense\(\s*<([\w]+)\s*\/>\s*\)\s*\}\s*\/>/g,
)) {
  const [, path, comp] = m;
  if (placeholderPages.has(comp)) placeholderRoutePaths.push(path);
}
let placeholderTargetHits = 0;
for (const { from, to } of REDIRECTS) {
  const targetPath = to.split("?")[0];
  if (placeholderRoutePaths.includes(targetPath)) {
    fail(
      `redirect 目标今天是「规划中」占位页：${from} → ${to}（来源若是今天可用的页面，` +
        `就是把用户从可用页送进占位页 = 功能性回退；Ruling 30 要求这种 redirect 退回「保留」）`
    );
    placeholderTargetHits += 1;
  }
}
if (!placeholderTargetHits) {
  pass(`redirect 目标无「规划中」占位页（占位路由共 ${placeholderRoutePaths.length} 条，均不是任何 redirect 目标）`);
}

// 7：站内链接不许再写**已迁移的旧裸路径**（新代码一律指规范路由）。
//    只收"旧路径恰好等于整串"的情形：`navigate("/research")`、`to="/tags"`、`` `/watchlist` ``。
//    带参旧路径（`/stock/xxx`、`/tags/xxx`）**不在此列**——它们有 redirect 兜底、行为等价，
//    全量改写属独立机械重构（本轮不混入），故不误报、也不假装已清零。
const FORBIDDEN_LITERALS = ["/research", "/watchlist", "/tags", "/index"];
const walked = [];
function walk(dir) {
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) walk(full);
    else if (/\.(ts|tsx)$/.test(entry.name)) walked.push(full);
  }
}
walk(join(root, "src"));
let literalHits = 0;
for (const file of walked) {
  const rel = file.slice(root.length + 1);
  if (rel === "src/app/router/index.tsx") continue; // 契约与 redirect 声明的所在地
  const text = readFileSync(file, "utf8");
  for (const literal of FORBIDDEN_LITERALS) {
    const esc = literal.replace(/[/\\^$*+?.()|[\]{}]/g, "\\$&");
    // 只看"字符串字面量整串"：前后紧邻引号才算命中；
    // 模板串 `` `/research/${key}` `` 或路径片段 `@/pages/research` 都不会误报
    const re = new RegExp(`["'\`]${esc}["'\`]`);
    if (re.test(text)) {
      fail(`站内链接写了已迁移的旧裸路径：${rel} 含 "${literal}"（应写规范路由）`);
      literalHits += 1;
    }
  }
}
if (!literalHits) pass("站内链接无已迁移旧裸路径字面量（router 内的 redirect 声明除外）");

// ── 9：IA §4 表 ↔ 净效果块声明的数字**双向比对** ─────────────────────────────
// 上一轮同类任务（Task 2）的教训：数字与成员必须成对书写并机械核对，否则文档自相矛盾而无人发现。
// 这里机械解析 IA §4 的三处：
//   (a) 迁移表的「处理」列 —— `redirect` / `保留*` 行与**成员**；
//   (b) 净效果块的「合计 A + B + C = N（= §4 表 redirect 行数）」与其分组成员；
//   (c) redirect 契约段的「8 条 redirect、7 条保留」。
// 三者 + 本脚本的契约数组必须一致。
const IA_DOC = join(root, "..", "docs/design/ia-investment-research-platform.md");
const ia = readFileSync(IA_DOC, "utf8");
const iaRedirects = [];
const iaKeeps = [];
for (const line of ia.split("\n")) {
  // 逐行按 `|` 分段，但**反引号内的 `|` 不算分隔**（如 `/market/heatmap?group=:category`）
  const cells = [];
  let buf = "";
  let inCode = false;
  for (const ch of line) {
    if (ch === "`") inCode = !inCode;
    if (ch === "|" && !inCode) {
      cells.push(buf);
      buf = "";
    } else {
      buf += ch;
    }
  }
  cells.push(buf);
  if (cells.length < 6) continue;
  const [rowNo, fromCell, , actionCell] = [cells[1], cells[2], cells[3], cells[4]];
  if (!/^\s*\d+\s*$/.test(rowNo)) continue;
  const fromMatch = fromCell.match(/`([^`]+)`/);
  if (!fromMatch) continue;
  const from = fromMatch[1].trim();
  const action = actionCell.trim();
  if (action === "redirect") iaRedirects.push(from);
  else if (action.startsWith("保留")) iaKeeps.push(from);
}
const cmp = (label, docList, scriptList) => {
  const a = [...docList].sort().join(" | ");
  const b = [...scriptList].sort().join(" | ");
  if (a === b) pass(`IA §4 表与脚本契约一致（${label}：${docList.length} 条）`);
  else fail(`IA §4 表与脚本契约不一致（${label}）\n      IA  = ${a}\n      脚本 = ${b}`);
};
cmp("redirect 成员", iaRedirects, REDIRECTS.map((r) => r.from));
cmp("保留成员", iaKeeps, KEEP);

// (b) 净效果块的分组合计
const groups = [...ia.matchAll(/^> - \*\*(\d+) 条\*\*[（(][^)）]*[)）]：([^—]+)——/gm)].map((m) => ({
  count: Number(m[1]),
  members: [...m[2].matchAll(/`([^`]+)`\s*→/g)].map((x) => x[1]),
}));
const sumDeclared = groups.reduce((acc, g) => acc + g.count, 0);
const sumMembers = groups.reduce((acc, g) => acc + g.members.length, 0);
if (groups.length && sumDeclared === sumMembers && sumDeclared === REDIRECTS.length) {
  pass(`净效果块算术自洽：分组合计 ${groups.map((g) => g.count).join(" + ")} = ${sumDeclared}，成员数 ${sumMembers}`);
} else {
  fail(
    `净效果块算术不自洽：声明的分组合计 ${groups.map((g) => g.count).join(" + ")} = ${sumDeclared}，` +
      `实际成员 ${sumMembers}，契约 ${REDIRECTS.length}`
  );
}
const sumLine = ia.match(/^> 合计 \*\*([\d\s+]+)= (\d+)\*\*/m);
if (sumLine) {
  const [, expr, total] = sumLine;
  const parts = expr.split("+").map((x) => Number(x.trim()));
  const exprSum = parts.reduce((a, b) => a + b, 0);
  if (exprSum === Number(total) && exprSum === REDIRECTS.length) {
    pass(`净效果块合计式自洽：${expr.trim()} = ${total}（= 契约 ${REDIRECTS.length}）`);
  } else {
    fail(`净效果块合计式不自洽：${expr.trim()} = ${total}，实际求和 ${exprSum}，契约 ${REDIRECTS.length}`);
  }
} else {
  fail("净效果块缺少 `> 合计 **a + b + c = N**` 形式的声明（无法机械比对）");
}

// (c) redirect 契约段声明的条数
const contractNums = ia.match(/\*\*(\d+) 条 redirect、(\d+) 条保留\*\*/);
if (contractNums) {
  const [, rd, kp] = [null, ...contractNums.slice(1)];
  if (Number(rd) === REDIRECTS.length && Number(kp) === KEEP.length) {
    pass(`redirect 契约段声明的条数与契约一致（${rd} redirect + ${kp} 保留）`);
  } else {
    fail(`redirect 契约段声明的条数不符：文档 ${rd} redirect + ${kp} 保留，契约 ${REDIRECTS.length} + ${KEEP.length}`);
  }
} else {
  fail("redirect 契约段缺少「N 条 redirect、M 条保留」的声明（无法机械比对）");
}

if (failed) {
  console.log(`\n路由迁移门禁未通过：${failed} 项`);
  process.exit(1);
}
console.log(`\n路由迁移门禁通过：旧 15 条全覆盖（${REDIRECTS.length} redirect + ${KEEP.length} 保留），${NEW_TARGETS.length} 条新目标已建立`);
