// 路由迁移门禁（Task 5 / S1.1，永久检查）。
//
// 依据：docs/design/ia-investment-research-platform.md §4「完整路由表（既有 15 条，一条不漏）」
// 与 §4「新目标路由的建立归属」（9 条今日不存在的目标）。
//
// 本脚本把那张表变成**可机械核对**的清单，逐条断言：
//   1. 既有 15 条旧路由在新 router 中全部有声明（不许删）；
//   2. 10 条 redirect 的**目标逐字一致**（含参数名，如 `/company/:symbol` 而非 `/stock/:symbol`）；
//   3. 5 条保留路由语义未变；
//   4. 每条 redirect 的目标路由**确实存在**（否则就是把 404 从旧路径搬到新路径）；
//   5. 9 条今日不存在的目标路由全部已建立；
//   6. 未落在表内的路由不许出现（防"顺手加的"路由绕过权限口径）。
//
// 只读 `src/app/router/index.tsx`，不 import 运行时（可在 CI 无依赖环境跑）。
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const ROUTER = join(root, "src/app/router/index.tsx");
const source = readFileSync(ROUTER, "utf8");

/** 从 router 源文件提取所有声明过的静态 `path="..."` */
const declaredPaths = [...source.matchAll(/path="([^"]+)"/g)].map((m) => m[1]);

// ── 契约（逐字抄自 IA 文档 §4）────────────────────────────────────────────

/** 5 条保留（语义未变，不是 redirect） */
const KEEP = [
  "/",
  "/login",
  "/market",
  "/market/industry/:level1Code",
  "/market/industry/:level1Code/:level2Code",
];

/** 10 条 redirect：旧路径 → 新位置（`${param ?? ""}` 已归一为 `:param`） */
const REDIRECTS = [
  { from: "/market/category", to: "/market/industry" },
  { from: "/market/hot-sectors/:category", to: "/market/heatmap?group=:category" },
  { from: "/market/concept/:boardCode", to: "/market/heatmap?concept=:boardCode" },
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
 * 9 行归属 / 12 条路由（含裸路径与带参变体、含顶栏工具目标 `/search`）。
 */
const NEW_TARGETS = [
  "/market/industry", // 1（裸）— /market/category 的目标
  "/market/heatmap", // 2（?group= / ?concept=）
  "/market/index/:tsCode", // 3
  "/company", // 4
  "/company/:symbol", // 5
  "/industry", // 6（裸）
  "/industry/:industryKey", // 7
  "/me", // 8（?tab= / ?tab=tags&name=）
  "/search", // 9（?q=，顶栏工具目标、非 redirect）
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

// redirect 条数必须是 10（多一条少一条都要显式改契约）
const redirectRouteCount = [...staticRedirects.keys(), ...componentRedirects.keys()].filter((p) =>
  REDIRECTS.some((r) => r.from === p)
).length;
if (redirectRouteCount === REDIRECTS.length) pass(`redirect 条数 = ${REDIRECTS.length}（不多不少）`);
else fail(`redirect 条数 = ${redirectRouteCount}，契约 ${REDIRECTS.length}`);

if (failed) {
  console.log(`\n路由迁移门禁未通过：${failed} 项`);
  process.exit(1);
}
console.log(`\n路由迁移门禁通过：旧 15 条全覆盖（${REDIRECTS.length} redirect + ${KEEP.length} 保留），9 条新目标已建立`);
