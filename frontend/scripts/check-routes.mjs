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
 * Ruling 30 后 = 8 行归属 / 11 条路由（含裸路径与带参变体、含顶栏工具目标 `/search`；
 * 其中 `/me` 一行承接 3 个 tab 变体）。
 * `/market/heatmap` 已移出本表（它不是任何 redirect 的目标，见下面「③ redirect 目标不得是占位页」断言）。
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

// ── 解析 Router：每条路由的 path + element 全文 ────────────────────────────
//
// Ruling 32：**不要**只从"N 条已知 redirect"出发去数——那样新增/漏掉的 `<Navigate>` 会被静默过滤掉。
// 这里先把 router 里**每一条** `<Route>` 的 `path` 与 `element` 全文取出来，再由 element 判定类别：
//   ① element 含 `<Navigate`（直接写或经 `Legacy*Redirect` 组件间接写）→ 它是 redirect；
//   ② 渲染的页面源文件带 `data-testid="placeholder-route-params"`（**正向标记**）→ 它是占位页；
//   ③ 其余 → 它是普通页面。
// 然后断言三类互斥且有界：redirect 集合 == REDIRECTS、KEEP ∩ redirect == ∅、占位 ∩ 目标 == ∅。

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

/** 拆出一条 `<Route ... />` 的 JSX 属性（用引号/大括号/括号配平切分，避免正则漏解嵌套） */
function parseRouteProps(tag) {
  const props = {};
  let i = "<Route".length;
  while (i < tag.length) {
    while (i < tag.length && /[\s/]/.test(tag[i])) i += 1;
    const nameMatch = /^([A-Za-z_][\w-]*)/.exec(tag.slice(i));
    if (!nameMatch) break;
    const name = nameMatch[1];
    i += name.length;
    while (i < tag.length && /\s/.test(tag[i])) i += 1;
    if (tag[i] !== "=") {
      props[name] = "true";
      continue;
    }
    i += 1;
    while (i < tag.length && /\s/.test(tag[i])) i += 1;
    if (tag[i] === '"' || tag[i] === "'") {
      const quote = tag[i];
      const close = tag.indexOf(quote, i + 1);
      props[name] = tag.slice(i + 1, close);
      i = close + 1;
    } else if (tag[i] === "{") {
      let depth = 0;
      let j = i;
      for (; j < tag.length; j += 1) {
        if (tag[j] === "{") depth += 1;
        else if (tag[j] === "}") {
          depth -= 1;
          if (depth === 0) break;
        }
      }
      props[name] = tag.slice(i + 1, j);
      i = j + 1;
    } else {
      i += 1;
    }
  }
  return props;
}

/** 切出所有顶层 `<Route ... />` 自闭合标签（页面的 layout 路由 `<Route element=...>` 无 path，跳过） */
const routeTags = [];
for (let idx = source.indexOf("<Route"); idx !== -1; ) {
  let depth = 0;
  let j = idx + "<Route".length;
  let endIdx = -1;
  for (; j < source.length; j += 1) {
    const ch = source[j];
    if (ch === "{") depth += 1;
    else if (ch === "}") depth -= 1;
    else if (ch === '"') {
      j = source.indexOf('"', j + 1);
    } else if (ch === "<" && source[j + 1] === "/") {
      // `<Route ...></Route>` 形式（本文件未用，容错）
      endIdx = source.indexOf(">", j) + 1;
      break;
    } else if (ch === "/" && source[j + 1] === ">" && depth === 0) {
      endIdx = j + 2;
      break;
    }
  }
  if (endIdx === -1) break;
  routeTags.push(source.slice(idx, endIdx));
  idx = source.indexOf("<Route", endIdx);
}

const declaredRoutes = routeTags
  .map(parseRouteProps)
  .filter((p) => typeof p.path === "string" && p.path.length > 0)
  .map((p) => ({ path: p.path, element: p.element ?? "" }));
if (declaredRoutes.length < 20) {
  fail(`路由解析异常：只解析出 ${declaredRoutes.length} 条带 path 的 <Route>（预期 ≥ 20，解析器可能失效）`);
}

/** `Legacy*Redirect` 组件名的函数体（用于把"经组件间接 redirect"也算成 redirect） */
function functionBodyOf(name) {
  const fnStart = source.indexOf(`function ${name}(`);
  if (fnStart === -1) return null;
  const open = source.indexOf("<Navigate", fnStart);
  if (open === -1) return null;
  const close = source.indexOf("/>", open);
  return source.slice(open, close + 2);
}

/** 一条路由是否落在 `<Navigate>` 上（直接写 / 经 `Legacy*Redirect` 组件） */
function redirectTargetOf(element) {
  if (!element.includes("Navigate") && !element.includes("Redirect")) return null;
  // 直接写：element={<Navigate to="X" replace />} / to={`模板`}
  const inlineLiteral = element.match(/<Navigate to="([^"]+)"/);
  if (inlineLiteral) return inlineLiteral[1];
  const inlineTemplate = element.match(/<Navigate to=\{`([^`]+)`\}/);
  if (inlineTemplate) return normalizeTarget(inlineTemplate[1]);
  // 经组件：element={<LegacyFooRedirect />}
  const comp = element.match(/<(Legacy\w+Redirect)\s*\/>/);
  if (comp) {
    const body = functionBodyOf(comp[1]);
    if (!body) return null;
    const literal = body.match(/<Navigate to="([^"]+)"/);
    if (literal) return literal[1];
    const template = body.match(/<Navigate to=\{`([^`]+)`\}/);
    if (template) return normalizeTarget(template[1]);
  }
  return null;
}

const routeRedirects = new Map(); // path -> 目标（normalize 后）
for (const { path, element } of declaredRoutes) {
  const target = redirectTargetOf(element);
  if (target !== null) routeRedirects.set(path, normalizeTarget(target));
}
const actualRedirects = routeRedirects;
const staticRedirects = new Map([...routeRedirects].filter(([, v]) => !v.includes(":")));
const componentRedirects = new Map([...routeRedirects].filter(([, v]) => v.includes(":")));

// ── 占位路由：**正向标记**，不靠"是否 import 了 PlaceholderPage"推断 ──────────
// 标记 = 页面源文件里出现 `data-testid="placeholder-route-params"`（PlaceholderPage 与
// market-heatmap 都带；后者自带「规划中」空态、并不 import PlaceholderPage，
// 旧判据会把它漏掉——这正是评审 F2(a) 证明的假绿来源）。
const PLACEHOLDER_MARKER = 'data-testid="placeholder-route-params"';
const lazyAliases = new Map(); // 组件常量名 -> 页面目录
for (const m of source.matchAll(
  /(\w+)\s*=\s*lazy\(\s*\(\)\s*=>\s*import\("@\/pages\/([\w-]+)(?:\/[\w-]+)?"\)\s*\)/g,
)) {
  lazyAliases.set(m[1], m[2]);
}
const placeholderComponents = new Set();
for (const [constName, dir] of lazyAliases) {
  const pageEntry = join(root, "src/pages", dir, "index.tsx");
  const pageSource = readFileSync(pageEntry, "utf8");
  if (pageSource.includes(PLACEHOLDER_MARKER)) placeholderComponents.add(constName);
}
const placeholderRoutePaths = declaredRoutes
  .filter(({ element }) =>
    [...placeholderComponents].some((comp) => new RegExp(`<${comp}\\s*/>`).test(element))
  )
  .map(({ path }) => path);
// 反向自证：占位组件数量与占位路由数量必须对得上（防止别名没匹配上导致集合为空）
const placeholderAliasCount = [...placeholderComponents].length;
if (placeholderAliasCount === 0 || placeholderRoutePaths.length === 0) {
  fail(`占位路由检测失效：识别到 ${placeholderAliasCount} 个占位组件、${placeholderRoutePaths.length} 条占位路由（不允许为 0）`);
}

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

// ── 顶栏 10 导航必须与 MainLayout 的 TOP_NAV 同源（防"删了一个导航没人发现"）──────
// NAV_ROOTS 此前是手抄副本：从顶栏删掉一个导航，门禁照样全绿（评审 Minor）。
// 这里机械解析 `MainLayout.tsx` 的 TOP_NAV 条目（`{ nav: { key, title, path, ... }, visibility: ... }`），
// 与 NAV_ROOTS 双向比对，并断言每条都有非空标题与 path。
const MAIN_LAYOUT = join(root, "src/app/layouts/MainLayout.tsx");
const layoutSource = readFileSync(MAIN_LAYOUT, "utf8");
const topNavEntries = [...layoutSource.matchAll(
  /nav:\s*\{\s*key:\s*"([^"]+)",\s*title:\s*"([^"]+)",\s*path:\s*"([^"]+)"/g,
)].map((m) => ({ key: m[1], title: m[2], path: m[3] }));
if (topNavEntries.length !== 10) {
  fail(`顶栏导航解析异常：MainLayout 的 TOP_NAV 解析出 ${topNavEntries.length} 条（预期 10）`);
} else {
  pass(`顶栏 10 个一级导航可机械解析（${topNavEntries.map((e) => e.title).join(" ")}）`);
}
const topNavPaths = topNavEntries.map((e) => e.path).sort();
if (topNavPaths.join("|") === [...NAV_ROOTS].sort().join("|")) {
  pass(`NAV_ROOTS 与 MainLayout.TOP_NAV 同源（${topNavPaths.length} 条，双向一致）`);
} else {
  fail(`NAV_ROOTS 与 MainLayout.TOP_NAV 不一致\n      顶栏 = ${topNavPaths.join("、")}\n      脚本 = ${[...NAV_ROOTS].sort().join("、")}`);
}
for (const e of topNavEntries) {
  if (!e.title.trim() || !e.path.trim()) fail(`顶栏导航缺标题或 path：${JSON.stringify(e)}`);
}
const navKeys = topNavEntries.map((e) => e.key).sort();
if (navKeys.join("|") === topNavPaths.join("|")) pass("TOP_NAV 的 key 与 path 一一对应");
else fail(`TOP_NAV 的 key 与 path 不对应：key=${navKeys.join("、")} path=${topNavPaths.join("、")}`);

// ── 每条已声明路由都必须挂到某个页面组件（防"声明了却没挂/挂错"）──────────────
// 覆盖评审指出的 `/market/heatmap` 无人断言问题：它必须挂到一个页面组件（本任务阶段是占位页）。
const routeWithoutElement = declaredRoutes.filter(
  ({ element }) => !/<[A-Z]/.test(element) && !/<Navigate/.test(element)
);
if (routeWithoutElement.length === 0) {
  pass(`每条已声明路由都挂到页面/重定向组件（${declaredRoutes.length} 条）`);
} else {
  fail(`以下路由未挂任何元素：${routeWithoutElement.map((r) => r.path).join("、")}`);
}
if (declaredPaths.includes("/market/heatmap")) {
  const heatmapRoute = declaredRoutes.find((r) => r.path === "/market/heatmap");
  if (heatmapRoute && placeholderRoutePaths.includes("/market/heatmap")) {
    pass("/market/heatmap 已声明且被识别为占位路由（S1.5 接管前不承诺数据）");
  } else {
    fail("/market/heatmap 未声明或未被识别为占位路由");
  }
} else {
  fail("/market/heatmap 未声明（S1.5 的落点必须先占位）");
}

// ── Ruling 32：三类互斥且有界的断言（每条都有独立反例，见报告 §2.12）──────────
// ① 凡 element 含 `<Navigate`（直接或经 `Legacy*Redirect`）的路由，其 path 必须 ∈ REDIRECTS
const redirectPaths = new Set([...actualRedirects.keys()]);
const contractRedirectPaths = new Set(REDIRECTS.map((r) => r.from));
const undeclaredRedirects = [...redirectPaths].filter((p) => !contractRedirectPaths.has(p));
if (undeclaredRedirects.length === 0) {
  pass(`① redirect 集合 == 契约（${redirectPaths.size} 条，无"未声明的 redirect"）`);
} else {
  fail(
    `① 存在未声明的 redirect 路由（element 含 <Navigate> 却不在 REDIRECTS）：${undeclaredRedirects.join("、")}` +
      ` —— 新增 redirect 必须同步改契约（否则条数会被静默过滤）`
  );
}
// ② KEEP 里任何一条 path 都**不得**是 `<Navigate>`（Ruling 30：保留 = 原地渲染真页面）
const keepThatRedirect = KEEP.filter((p) => redirectPaths.has(p));
if (keepThatRedirect.length === 0) {
  pass(`② 7 条保留均非 redirect（无 KEEP 行被偷换成 <Navigate>）`);
} else {
  fail(
    `② 保留路由被改成了 redirect：${keepThatRedirect.join("、")}` +
      ` —— 「保留（语义未变）」必须是原地渲染真页面（Ruling 30/31）`
  );
}
// ③ 任何 redirect 的目标都不得是「规划中」占位页
const placeholderTargets = [...actualRedirects.entries()].filter(([, to]) =>
  placeholderRoutePaths.includes(to.split("?")[0])
);
if (placeholderTargets.length === 0) {
  pass(
    `③ redirect 目标无「规划中」占位页（占位路由共 ${placeholderRoutePaths.length} 条：${placeholderRoutePaths.join("、")}）`
  );
} else {
  fail(
    `③ redirect 目标是「规划中」占位页：${placeholderTargets.map(([f, t]) => `${f} → ${t}`).join("、")}` +
      ` —— 来源今天有真页面时这是功能性回退，应退回「保留」`
  );
}

// redirect 条数必须与契约一致（多一条少一条都要显式改契约；Ruling 30 后 = 8）
const redirectRouteCount = [...actualRedirects.keys()].filter((p) => contractRedirectPaths.has(p)).length;
if (redirectRouteCount === REDIRECTS.length) pass(`redirect 条数 = ${REDIRECTS.length}（不多不少）`);
else fail(`redirect 条数 = ${redirectRouteCount}，契约 ${REDIRECTS.length}`);

// 保留条数同样必须一致（Ruling 30 后 = 7）
const keepDeclared = KEEP.filter((p) => declaredPaths.includes(p)).length;
if (keepDeclared === KEEP.length) pass(`保留条数 = ${KEEP.length}（不多不少）`);
else fail(`保留条数 = ${keepDeclared}，契约 ${KEEP.length}`);

// 7：站内链接不许再写**已迁移的旧裸路径**（新代码一律指规范路由）。
//    只看"字符串字面量整串"，并且**跳过 import/export 行**——模块说明符
//    （`import { X } from "@/shared/ui"`）会被朴素正则误判成路由字面量。
//    带参旧路径（`/stock/xxx`、`/tags/xxx`）**不在此列**：有 redirect 兜底、行为等价，
//    全量改写属独立机械重构（本轮 deferred），故不误报、也不假装已清零。
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
  const lines = readFileSync(file, "utf8").split("\n");
  for (const [idx, line] of lines.entries()) {
    if (/^\s*(import|export)\b/.test(line)) continue; // 模块说明符不是路由
    if (/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(line)) continue; // 纯注释行（路径说明不是链接目标）
    for (const literal of FORBIDDEN_LITERALS) {
      const esc = literal.replace(/[/\\^$*+?.()|[\]{}]/g, "\\$&");
      if (new RegExp(`["'\`]${esc}["'\`]`).test(line)) {
        fail(`站内链接写了已迁移的旧裸路径：${rel}:${idx + 1} 含 "${literal}"（应写规范路由）`);
        literalHits += 1;
      }
    }
  }
}
if (!literalHits) pass("站内链接无已迁移旧裸路径字面量（import/export 行与 router 内的 redirect 声明除外）");

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
