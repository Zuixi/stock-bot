// 路由迁移门禁（Task 5 / S1.1，**永久检查**）。
//
// 依据：docs/design/ia-investment-research-platform.md §4「完整路由表（既有 15 条，一条不漏）」
// 与 §4「新目标路由的建立归属」（Ruling 30 后：8 条今日不存在的 redirect 目标）。
//
// **设计（Ruling 33）**：断言的数据来自 `src/app/router/route-manifest.ts` —— 路由清单是唯一真相，
// `router/index.tsx` 由它生成 `<Route>`。**本脚本不解析任何 JSX 源码**：此前两轮"解析 router"
// 连续假绿（占位判定对共享组件失效、扫描器吞掉 `/` 与 `/login`），故彻底移除该层。
//
// 断言清单：
//   1. 既有 15 条旧路由在清单中全部存在（不许删）；8 条 redirect / 7 条保留；
//   2. 8 条 redirect 的**目标逐字一致**（含参数名，如 `/company/:symbol` 而非 `/stock/:symbol`）；
//   3. 每条 redirect 的目标都能命中某条已声明路由（否则就是把 404 从旧路径搬到新路径）；
//   4. 8 条今日不存在的目标路由已建立；10 个一级导航根可达（`NAV_ROOTS` 即顶栏数据源）；
//   5. 未落在契约内的路由不许出现（防"顺手加的"路由绕过权限口径）；
//   6. **三类互斥**：redirect 集合 == 契约、保留行不得是 redirect、redirect 目标不得是占位页（`isPlaceholder`）；
//      且**占位集合的成员与基数是被断言的契约**（`PLACEHOLDER_CONTRACT`）+ `/market/heatmap` 定点断言
//      —— 旧版只守"集合非空"，摘掉一条 `isPlaceholder` 就假绿（Task 5 re-review 实测）；
//   7. 站内链接不得再写已迁移的旧裸路径；不含 `/calendar`；
//   8. **IA §4 文档 ↔ 清单契约 双向比对**（表行成员、净效果块分组合计与成员数、合计式、契约段声明数字）；
//   9. **ARCHITECTURE（前端当前态权威）§6 路由一览 ↔ 清单 双向比对**（表尾「为准」指针句 + 每行路径唯一 +
//      路径集合 + `kind` + 页面目录/组件 + redirect 去向 + 鉴权口径；详见文件尾断言 9 的注释）。

import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");

// ── 清单（唯一真相）：直接 import，不做源码解析 ───────────────────────────────
// node 直接跑 `.ts`（type stripping）；本文件里没有类型注解/枚举等需要转译的语法。
const {
  ROUTES,
  NAV_ROOTS,
  getPageRoutes,
  getRedirectRoutes,
  getPlaceholderRoutes,
  getRoutePaths,
} = await import(pathToFileURL(join(root, "src/app/router/route-manifest.ts")).href);

const declaredRoutes = ROUTES;
const declaredPaths = getRoutePaths();
const routeRedirects = new Map(getRedirectRoutes().map((r) => [r.path, r.redirectTo]));

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

/** 8 条 redirect：旧路径 → 新位置（`redirectTo` 即契约写法） */
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
 * §4「新目标路由的建立归属」——**今日不存在的目标**（本任务已新建）。
 * Ruling 30 后 = 8 行归属；`/market/heatmap` 已移出本表（它不是任何 redirect 的目标）。
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

/**
 * IA §4「S1.x 新增路由的登记（Ruling 56.1）」——Wave 1 起新增的**真功能页**。
 *
 * 为什么从 `ALLOWED_EXTRA` 提出来单列：白名单只表达"允许出现"，不表达"契约要求它存在"，
 * 于是 4 条真路由一度只活在门禁脚本里、契约文档 §4 里查不到（review 实测）。本常量与 IA §4
 * 那张表**双向比对**（见下方断言 8b）：表 ↔ 清单路径集合一致 + 声明条数一致。
 * 它们**不是 redirect**，故不参与 `REDIRECTS` / `KEEP` 的任何算术。
 */
const NEW_S1_ROUTES = [
  "/market/global", // S1.3 全球市场
  "/market/industry/:level1Code/:level2Code/:level3Code", // S1.4 申万末级成分股
  "/market/heatmap", // S1.5 板块热力图（Ruling 30：不是任何 redirect 的目标）
  "/market/data-sources", // Phase 1 §W2.1 数据版图独立页（前端设计整改 §0-E 裁定：市场域内）
  "/market/rankings/:key", // S1.6 A股排行 7 榜
  "/admin/users", // PR #15 管理员控制台（入口在账户菜单，不在顶栏 NAV_ROOTS）
  "/change-password", // PR #15 强制首登改密页（只要"已登录"、不加权限）
];

/** 允许出现的额外路由（既有页面按新前缀搬迁 / 目标带参变体）：超出即报错，防私加路由 */
const ALLOWED_EXTRA = [
  ...NAV_ROOTS.map((n) => n.path),
  "/market/index/:tsCode",
  "/company/:symbol",
  "/industry/:industryKey",
  "/me",
  "/search",
  "/market/industry",
  // MainLayout 外壳（无 path）
  "",
];

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
  const actual = routeRedirects.get(from);
  if (actual === undefined) fail(`redirect 未实现：${from} → ${to}`);
  else if (actual !== to) fail(`redirect 目标不符：${from} → ${actual}（契约 ${to}）`);
  else pass(`redirect 一致：${from} → ${to}`);
}

// 3 + 4：redirect 目标可达 + 8 条目标路由已建立
for (const { from, to } of REDIRECTS) {
  if (targetResolves(to, declaredPaths)) pass(`redirect 目标可达：${to} ← ${from}`);
  else fail(`redirect 目标仍是 404：${to}（来自 ${from}）`);
}
for (const target of NEW_TARGETS) {
  if (declaredPaths.includes(target)) pass(`新目标路由已建立：${target}`);
  else fail(`新目标路由缺失：${target}`);
}

// 10 个导航根可达（清单即真相）
for (const nav of NAV_ROOTS) {
  if (declaredPaths.includes(nav.path)) pass(`一级导航根可达：${nav.path}`);
  else fail(`一级导航根缺失：${nav.path}`);
}
if (NAV_ROOTS.length === 10) pass(`顶栏一级导航 = ${NAV_ROOTS.length} 个（IA §1）`);
else fail(`顶栏一级导航 = ${NAV_ROOTS.length} 个，契约 10 个`);
for (const nav of NAV_ROOTS) {
  if (!nav.title || !String(nav.title).trim()) fail(`导航缺标题：${nav.path}`);
}
if (new Set(NAV_ROOTS.map((n) => n.path)).size === NAV_ROOTS.length) {
  pass("顶栏导航 path 无重复");
} else {
  fail("顶栏导航 path 有重复");
}

// 结构断言（Ruling 33 回归）：**除以 `/` 独立布局外，所有路由都必须嵌在 MainLayout 下**。
// 依据：改造前的 router 里 MainLayout 是唯一有子路由的 layout 路由，顶栏/页脚由它渲染；
// 路由清单若漏写 `parent`，页面会以"无布局"渲染（顶栏消失）而门禁察觉不到。
const layoutKeys = new Set(declaredRoutes.filter((r) => r.kind === "layout").map((r) => r.key));
const badParent = declaredRoutes.filter((r) => r.parent && !layoutKeys.has(r.parent));
if (badParent.length === 0) pass(`所有 parent 都指向已声明的 layout 路由（${layoutKeys.size} 个布局）`);
else fail(`parent 指向不存在的 layout：${badParent.map((r) => `${r.path} → ${r.parent}`).join("、")}`);
// 计数**从实际参与断言的路由集合取数**：`mustBeUnderLayout` 就是被断言的集合本身。
// 不要再写 `declaredPaths.length - 2` —— `getRoutePaths()` 本就不含无 path 的 layout 路由，
// 再手工减 2 会少算一条（Task 5 re-review 实测：文案打印 29，实际 30 条满足断言）。
// "手工 ± 常量"正是文案与断言各说各话的温床，三轮假绿里有它一份。
const mustBeUnderLayout = declaredRoutes.filter((r) => r.kind !== "layout" && r.path !== "/");
const notUnderLayout = mustBeUnderLayout.filter((r) => r.parent !== "main");
if (notUnderLayout.length === 0) {
  pass(`除以 \`/\`（landing 独立布局）外全部路由都嵌在 MainLayout 下（${mustBeUnderLayout.length} 条）`);
} else {
  fail(
    `以下路由未嵌在 MainLayout 下（顶栏/页脚不会渲染）：${notUnderLayout.map((r) => r.path).join("、")}` +
      ` —— 清单里要写 parent: "main"`
  );
}
if (declaredRoutes.some((r) => r.path === "/" && r.parent)) {
  fail("landing \`/\` 不该有 parent（它不套 MainLayout）");
} else {
  pass("landing `/` 无 parent（独立布局，与改造前一致）");
}

// 5：不许出现表外路由
const contract = new Set([...legacy, ...NEW_TARGETS, ...NEW_S1_ROUTES, ...ALLOWED_EXTRA]);
let offContract = 0;
for (const declared of declaredPaths) {
  if (!contract.has(declared)) {
    fail(`表外路由（未经 IA 冻结，需先改契约）：${declared}`);
    offContract += 1;
  }
}
if (!offContract) pass(`清单内 ${declaredPaths.length} 条路由全部在契约内`);

// 严禁 `/calendar`（IA §3「明确不含 /calendar」）
if (declaredPaths.includes("/calendar")) fail("出现了被明确排除的 /calendar");
else pass("不含 /calendar");

// 6：三类互斥（Ruling 32/33）——清单即数据，无需解析源码
const redirectPaths = new Set(routeRedirects.keys());
const contractRedirectPaths = new Set(REDIRECTS.map((r) => r.from));
const undeclaredRedirects = [...redirectPaths].filter((p) => !contractRedirectPaths.has(p));
if (undeclaredRedirects.length === 0) {
  pass(`① redirect 集合 == 契约（${redirectPaths.size} 条，无"未声明的 redirect"）`);
} else {
  fail(
    `① 存在未声明的 redirect 路由（清单 kind="redirect" 却不在契约）：${undeclaredRedirects.join("、")}` +
      ` —— 新增 redirect 必须同步改契约`
  );
}
const keepThatRedirect = KEEP.filter((p) => redirectPaths.has(p));
if (keepThatRedirect.length === 0) {
  pass(`② ${KEEP.length} 条保留均非 redirect（无保留行被偷换成 redirect）`);
} else {
  fail(
    `② 保留路由被改成了 redirect：${keepThatRedirect.join("、")}` +
      ` —— 「保留（语义未变）」必须原地渲染真页面（Ruling 30/31）`
  );
}
/**
 * 占位路由契约（与 `NEW_TARGETS` / `REDIRECTS` **同级**）：今日「规划中」的占位页清单。
 *
 * 为什么要有它（Task 5 re-review 实测的假绿）：旧版只断言"占位集合非空"，于是**只摘掉 `/company`
 * 的 `isPlaceholder` 标志**（保留契约一致的 `/watchlist → /company` redirect）就能得到
 * exit 0 + 「占位路由共 9 条」——而摘标志 = 占位通告壳消失 = 功能性回退。
 * 故把**成员与基数**都变成被断言的契约：多一条、少一条都转红。
 */
const PLACEHOLDER_CONTRACT = [
  // `/market/heatmap` 已随 S1.5 兑现真实现摘除占位（S1.5 落地前它是契约成员，
  // 定点断言曾守「不得摘标志冒充真页面」；现在反向守「真页面不许退回占位」，
  // 见下方定点断言——同一行占位契约的两个方向各守各的时期）。
  // `/sentiment` 已随 S1.8–S1.13 兑现真页面（综合指数 hero + 9 个模块面板）摘除占位，
  // 并在清单里改为 `requiresAuth`（消费受保护的 /api/v1/metrics/*）——同样只许前进不许退回。
  "/company",
  "/news",
  "/macro",
  "/valuegraph",
  "/strategy",
  "/risk",
  "/trade",
  "/search",
];

const placeholderPaths = getPlaceholderRoutes().map((r) => r.path);
const placeholderMissing = PLACEHOLDER_CONTRACT.filter((p) => !placeholderPaths.includes(p));
const placeholderExtra = placeholderPaths.filter((p) => !PLACEHOLDER_CONTRACT.includes(p));
if (placeholderMissing.length === 0 && placeholderExtra.length === 0) {
  pass(`占位路由集合 == 契约（${PLACEHOLDER_CONTRACT.length} 条，成员逐条一致）`);
} else {
  fail(
    `占位路由集合与契约不符（契约 ${PLACEHOLDER_CONTRACT.length} 条，实得 ${placeholderPaths.length} 条）` +
      (placeholderMissing.length ? `\n      清单缺 isPlaceholder：${placeholderMissing.join("、")}` : "") +
      (placeholderExtra.length ? `\n      清单多 isPlaceholder：${placeholderExtra.join("、")}` : "")
  );
}
// 定点断言（旧脚本能力，Task 5 re-review 要求恢复）：`/market/heatmap` 的占位状态是被断言的契约。
// S1.5 落地**前**它守「不得摘标志冒充真页面」；S1.5 兑现后**反向**守「真页面不许退回占位」
// ——同一行路由的两个方向各守各的时期，断言永不空转。
if (!placeholderPaths.includes("/market/heatmap")) {
  pass("`/market/heatmap` 已摘占位标志（S1.5 真热力图已落地，不得退回占位冒充「规划中」）");
} else {
  fail("`/market/heatmap` 仍是占位路由 —— S1.5 已兑现真实现，退回占位 = 功能性回退");
}

const placeholderTargets = [...routeRedirects.entries()].filter(([, to]) =>
  placeholderPaths.includes(to.split("?")[0])
);
if (placeholderTargets.length === 0) {
  pass(
    `③ redirect 目标无「规划中」占位页（占位路由共 ${placeholderPaths.length} 条：${placeholderPaths.join("、")}）`
  );
} else {
  fail(
    `③ redirect 目标是「规划中」占位页：${placeholderTargets.map(([f, t]) => `${f} → ${t}`).join("、")}` +
      ` —— 来源今天有真页面时这是功能性回退，应退回「保留」`
  );
}

// 占位路由的保护面：每个占位路由都必须真的挂着一个页面组件
const placeholderWithoutComponent = getPlaceholderRoutes().filter((r) => !r.component);
if (placeholderWithoutComponent.length === 0) {
  pass(`占位路由都挂了页面组件（${placeholderPaths.length} 条）`);
} else {
  fail(`占位路由未挂页面组件：${placeholderWithoutComponent.map((r) => r.path).join("、")}`);
}

// 占位保护面（Ruling 33 回归）：占位路由里的**每一条页面**，其懒加载入口（`route-elements.tsx` 的 PAGES）
// 必须存在；且 `route-elements.tsx` 的占位通告映射必须覆盖全部占位路由——两份清单不许漂移。
// 注：注册表条目一律写单行 `Key: lazy(() => import("@/pages/<dir>"))`（这是本探针的输入格式）。
// 若有人改成别的写法，这里**会响亮报成"未注册"**（而不是静默跳过）——保持这个失败模式，别去放宽它。
const elementsSource = readFileSync(join(root, "src/app/router/route-elements.tsx"), "utf8");
const noticeKeys = [...elementsSource.matchAll(/^\s*"([^"]+)":\s*\{/gm)].map((m) => m[1]);
const missingNotice = placeholderPaths.filter((p) => !noticeKeys.includes(p));
if (missingNotice.length === 0) {
  pass(`占位通告映射覆盖全部占位路由（${placeholderPaths.length} 条）`);
} else {
  fail(`占位路由缺少 PLACEHOLDER_NOTICE 文案：${missingNotice.join("、")}`);
}
/** component 键 → 页面目录（`@/pages/<dir>`）：断言 9 用它核对 ARCHITECTURE §6 的"页面目录"列。
 *  取不到目录时断言 9 会**响亮**报"页面目录不可解析"（而不是静默跳过）——注册项一律保持
 *  单行 `Key: lazy(() => import("@/pages/<dir>"))` 形态。 */
const pageModules = new Map(
  [...elementsSource.matchAll(/^\s*(\w+):\s*lazy\(\(\) => import\("@\/pages\/([^"]+)"\)\)/gm)].map((m) => [
    m[1],
    m[2],
  ])
);
const pageComponentKeys = new Set(
  // 注册探针用**宽**正则（只认 `Key: lazy(`）：格式变化不该被误报成"没注册"；
  // 目录解析用**窄**正则（上面那条），解析不到时由断言 9 明确报"不可解析"。
  [...elementsSource.matchAll(/^\s*(\w+):\s*lazy\(/gm)].map((m) => m[1])
);
const missingComponent = getPageRoutes()
  .filter((r) => !pageComponentKeys.has(r.component))
  .map((r) => `${r.path} → ${r.component}`);
if (missingComponent.length === 0) {
  pass(`清单里每条 page 路由的 component 都已注册到 PAGES（${getPageRoutes().length} 条）`);
} else {
  fail(`清单引用了未注册的 component：${missingComponent.join("、")}`);
}

// redirect 条数必须与契约一致（Ruling 30 后 = 8）
const redirectRouteCount = [...redirectPaths].filter((p) => contractRedirectPaths.has(p)).length;
if (redirectRouteCount === REDIRECTS.length) pass(`redirect 条数 = ${REDIRECTS.length}（不多不少）`);
else fail(`redirect 条数 = ${redirectRouteCount}，契约 ${REDIRECTS.length}`);

// 保留条数必须一致（Ruling 30 后 = 7）
const keepDeclared = KEEP.filter((p) => declaredPaths.includes(p)).length;
if (keepDeclared === KEEP.length) pass(`保留条数 = ${KEEP.length}（不多不少）`);
else fail(`保留条数 = ${keepDeclared}，契约 ${KEEP.length}`);

// redirect 必须带 redirectTo（防"声明了 redirect 却没有目标"）
const redirectMissingTarget = getRedirectRoutes().filter((r) => !r.redirectTo);
if (redirectMissingTarget.length === 0) pass("每条 redirect 都声明了 redirectTo");
else fail(`redirect 缺少 redirectTo：${redirectMissingTarget.map((r) => r.path).join("、")}`);

// 7：站内链接不许再写**已迁移的旧裸路径**（新代码一律指规范路由）。
//    只跳过 import/export 行与纯注释行（模块说明符与路径说明不是链接目标）；
//    带参旧路径（`/stock/xxx`、`/tags/xxx`）**不在此列**：有 redirect 兜底、行为等价。
const FORBIDDEN_LITERALS = ["/research", "/watchlist", "/tags", "/index"];
const QUOTES = ['"', "'", "`"];
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
  if (rel === "src/app/router/route-manifest.ts") continue; // 契约与 redirect 声明的所在地
  const lines = readFileSync(file, "utf8").split("\n");
  for (const [idx, line] of lines.entries()) {
    if (/^\s*(import|export)\b/.test(line)) continue; // 模块说明符不是路由
    if (/^\s*(\/\/|\*|\/\*|\{\/\*)/.test(line)) continue; // 纯注释行
    for (const literal of FORBIDDEN_LITERALS) {
      // 只看"字符串字面量整串"：前后紧邻引号（' " `）才算命中——
      // 模板串 `` `/research/${key}` `` 或路径片段 `@/pages/research` 都不会误报
      const hit = QUOTES.some((q) => line.includes(q + literal + q));
      if (hit) {
        fail(`站内链接写了已迁移的旧裸路径：${rel}:${idx + 1} 含 "${literal}"（应写规范路由）`);
        literalHits += 1;
      }
    }
  }
}
if (!literalHits) pass("站内链接无已迁移旧裸路径字面量（import/export 行、注释行与清单文件除外）");

// 8：IA §4 表 ↔ 净效果块声明的数字**双向比对**（数字与成员必须成对书写并机械核对）
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
  const rowNo = cells[1];
  const fromCell = cells[2];
  const actionCell = cells[4];
  if (!/^\s*\d+\s*$/.test(rowNo)) continue;
  const fromMatch = fromCell.match(/`([^`]+)`/);
  if (!fromMatch) continue;
  const from = fromMatch[1].trim();
  const action = actionCell.trim();
  if (action === "redirect") iaRedirects.push(from);
  else if (action.startsWith("保留")) iaKeeps.push(from);
}
const cmp = (label, docList, manifestList) => {
  const a = [...docList].sort().join(" | ");
  const b = [...manifestList].sort().join(" | ");
  if (a === b) pass(`IA §4 表与清单契约一致（${label}：${docList.length} 条）`);
  else fail(`IA §4 表与清单契约不一致（${label}）\n      IA   = ${a}\n      清单 = ${b}`);
};
cmp("redirect 成员", iaRedirects, REDIRECTS.map((r) => r.from));
cmp("保留成员", iaKeeps, KEEP);

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
  const expr = sumLine[1];
  const total = sumLine[2];
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

const contractNums = ia.match(/\*\*(\d+) 条 redirect、(\d+) 条保留\*\*/);
if (contractNums) {
  const rd = contractNums[1];
  const kp = contractNums[2];
  if (Number(rd) === REDIRECTS.length && Number(kp) === KEEP.length) {
    pass(`redirect 契约段声明的条数与契约一致（${rd} redirect + ${kp} 保留）`);
  } else {
    fail(`redirect 契约段声明的条数不符：文档 ${rd} redirect + ${kp} 保留，契约 ${REDIRECTS.length} + ${KEEP.length}`);
  }
} else {
  fail("redirect 契约段缺少「N 条 redirect、M 条保留」的声明（无法机械比对）");
}

// 8b：IA §4「S1.x 新增路由的登记（Ruling 56.1）」表 ↔ `NEW_S1_ROUTES` **双向比对**。
// 为什么要有这条（review 实测的缺口）：这 4 条真路由一度只写在 `ALLOWED_EXTRA` 白名单里，
// 白名单只表达"允许出现"，不表达"契约要求存在" → 路由在契约文档 §4 中不可见。
// 故把该表变成被断言的契约：**路径集合双向**（表里有清单没有 = 幽灵行；清单有表里没有 = 文档漏记）
// + **声明条数**（`> **N 条 S1.x 新增路由**`：多一条少一条都转红）。
// 注意本表与 15 条旧路由表**列数不同**（4 列 vs 5 列），故不会被上面的旧表解析器吞掉——
// 下面按「恰 4 个单元格 + 第 1 格反引号里是 `/` 开头的路径」识别本表，不猜列位。
const iaS1Paths = [];
for (const line of ia.split("\n")) {
  if (!line.trim().startsWith("|")) continue;
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
  const trimmed = cells.slice(1, -1).map((c) => c.trim());
  if (trimmed.length !== 4) continue; // 旧路由表是 5 列，本表是 4 列
  const m = trimmed[0].match(/`(\/[^`]*)`/);
  if (m) iaS1Paths.push(m[1]);
}
{
  const a = [...iaS1Paths].sort().join(" | ");
  const b = [...NEW_S1_ROUTES].sort().join(" | ");
  if (a === b) {
    pass(`IA §4「S1.x 新增路由」表与清单契约一致（${iaS1Paths.length} 条）`);
  } else {
    fail(
      `IA §4「S1.x 新增路由」表与清单契约不一致\n      IA   = ${a}\n      清单 = ${b}` +
        ` —— 新增真功能路由必须同时登记 IA §4 本表与 check-routes.mjs 的 NEW_S1_ROUTES`
    );
  }
}
// S1.x 新增路由必须**真的在清单里声明**（表里写了、清单没有 = 文档承诺了不存在的路由）
for (const path of NEW_S1_ROUTES) {
  if (declaredPaths.includes(path)) pass(`S1.x 新增路由已声明：${path}`);
  else fail(`S1.x 新增路由缺失：${path}（IA §4 已登记，清单里却没有）`);
}
const s1CountLine = ia.match(/\*\*S1\.x 新增路由共 (\d+) 条\*\*/) || ia.match(/\*\*共 (\d+) 条\*\*/);
if (s1CountLine) {
  if (Number(s1CountLine[1]) === NEW_S1_ROUTES.length) {
    pass(`IA §4「S1.x 新增路由」声明的条数与契约一致（${s1CountLine[1]} 条）`);
  } else {
    fail(
      `IA §4「S1.x 新增路由」声明的条数不符：文档 ${s1CountLine[1]} 条，契约 ${NEW_S1_ROUTES.length} 条`
    );
  }
} else {
  fail("IA §4「S1.x 新增路由」块缺少「**S1.x 新增路由共 N 条**」的声明（无法机械比对）");
}

// 9：ARCHITECTURE（前端**当前态权威**，见 docs/authority.md）§6 路由一览 ↔ 清单**双向比对**。
// 为什么要有这条：§6 是"路由当前态"的单点权威，Task 5 改完 15 条路由后它整表过期、表尾指针还写
// 「以 app/router/index.tsx 为准」（那时 index.tsx 已不手写 `<Route>`）。靠人眼复核文档 = 又一处假绿，
// 故把它机械化：**路径集合双向**（表里有清单没有 = 幽灵路由；清单有表里没有 = 文档漏记）+ **每行路径唯一**
// + 逐行字段（kind / 页面目录 / 组件 / redirect 去向 / 鉴权）+ **表尾指针句**（见下方注释，别退回恒真断言）。
// 破坏验证：删/复制表里任一行、改任一 kind 或去向、把表尾指针改回 index.tsx → 都应转红。
const ARCH_DOC = join(root, "..", "docs/architecture/frontend/ARCHITECTURE.md");
const archDoc = readFileSync(ARCH_DOC, "utf8");
const sec6 = archDoc.split(/^## /m).find((s) => s.startsWith("6. "));
if (!sec6) {
  fail("ARCHITECTURE.md 找不到「## 6. 路由一览」小节（前端当前态权威文档）");
} else {
  // 指针断言必须落在**表尾指针句**上。踩过的坑（re-review F1）：原先只断言 `sec6.includes("route-manifest.ts")`，
  // 而 §6 **头部**（"index.tsx 由清单生成 `<Route>`"）恒含该串 → 把表尾改回「以 `app/router/index.tsx` 为准」
  // 仍然 exit 0。这就是本任务要消灭的"看着在、其实不触发"。故只认「为准」句，且逐句核对指向：
  const pointerLines = sec6.split("\n").filter((l) => l.includes("为准"));
  const badPointer = pointerLines.filter((l) => !l.includes("route-manifest.ts") || l.includes("index.tsx"));
  if (pointerLines.length === 0) {
    fail("ARCHITECTURE §6 缺「以 … 为准」的表尾指针句（没有它就无法机械判定谁是权威来源）");
  } else if (badPointer.length === 0) {
    pass(`ARCHITECTURE §6 指针句指向 \`route-manifest.ts\`（\"为准\"句 ${pointerLines.length} 条，无一条指向 index.tsx）`);
  } else {
    fail(
      "ARCHITECTURE §6 指针句未指向 `route-manifest.ts`（表尾指针必须写清单，index.tsx 只由清单生成 `<Route>`）：" +
        `\n      ${badPointer.map((l) => (l.trim().length > 90 ? `${l.trim().slice(0, 90)}…` : l.trim())).join("\n      ")}`
    );
  }

  const docRowList = [];
  const layoutRows = [];
  for (const line of sec6.split("\n")) {
    if (!line.trim().startsWith("|")) continue;
    const cells = line
      .split("|")
      .slice(1, -1)
      .map((c) => c.trim());
    if (cells.length < 5) continue;
    const pathCell = cells[0].match(/`(\/[^`]*)`/);
    if (pathCell) docRowList.push({ path: pathCell[1], cells });
    else if (cells[1] === "layout") layoutRows.push(cells);
  }
  // 用 Map 存行会让**重复行静默塌陷**（re-review F2：复制一条 `/login` 后门禁仍打印「表 31 条」并 exit 0），
  // 故先按 list 查重，再建 Map。
  const docPathList = docRowList.map((r) => r.path);
  const dupPaths = [...new Set(docPathList.filter((p, i) => docPathList.indexOf(p) !== i))];
  if (dupPaths.length === 0) {
    pass(`ARCHITECTURE §6 每行路径唯一（${docPathList.length} 行，无重复）`);
  } else {
    fail(`ARCHITECTURE §6 有重复行（同一路由只许出现一次）：${dupPaths.join("、")}`);
  }
  const docRows = new Map(docRowList.map((r) => [r.path, r.cells]));
  if (layoutRows.length === 1) pass("ARCHITECTURE §6 含且仅含 1 行 `kind = layout` 的 MainLayout 外壳行");
  else fail(`ARCHITECTURE §6 的 \`kind = layout\` 外壳行 = ${layoutRows.length} 行，应为 1 行`);

  const manifestPathList = declaredRoutes.filter((r) => r.kind !== "layout").map((r) => r.path);
  const missingInDoc = manifestPathList.filter((p) => !docRows.has(p));
  const extraInDoc = [...docRows.keys()].filter((p) => !manifestPathList.includes(p));
  if (missingInDoc.length === 0 && extraInDoc.length === 0) {
    pass(
      `ARCHITECTURE §6 表 ↔ 清单**路径集合双向一致**（表 ${docPathList.length} 行 / 清单 ${manifestPathList.length} 条）`
    );
  } else {
    fail(
      "ARCHITECTURE §6 表与清单路径集合不一致" +
        (missingInDoc.length ? `\n      清单有、表里没有：${missingInDoc.join("、")}` : "") +
        (extraInDoc.length ? `\n      表里有、清单没有：${extraInDoc.join("、")}` : "")
    );
  }

  const docDrift = [];
  for (const route of declaredRoutes) {
    if (route.kind === "layout") continue;
    const cells = docRows.get(route.path);
    if (!cells) continue;
    // 先看 `kind` 再看 `isPlaceholder`：反过来的写法对非 page 路由是**死代码**（F3）。
    const expectKind = route.kind === "page" && route.isPlaceholder ? "page（占位）" : route.kind;
    if (cells[1] !== expectKind) docDrift.push(`${route.path}：kind 列 = ${cells[1]}，应为 ${expectKind}`);
    if (route.kind === "page" && route.component) {
      const dir = pageModules.get(route.component);
      if (!dir) {
        docDrift.push(`${route.path}：页面目录不可解析（${route.component} 未注册成 \`lazy(() => import("@/pages/<dir>"))\`）`);
      } else if (!cells[2].includes(`pages/${dir}`)) {
        docDrift.push(`${route.path}：页面目录应为 pages/${dir}`);
      }
      if (!cells[2].includes(`\`${route.component}\``)) docDrift.push(`${route.path}：未写出组件名 ${route.component}`);
    }
    if (route.kind === "redirect" && !cells[2].includes(`→ \`${route.redirectTo}\``)) {
      docDrift.push(`${route.path}：去向应为 → \`${route.redirectTo}\``);
    }
    // 鉴权列**双向**核对：清单有门禁 → 表里必须写出来（防"代码加了门禁文档没写"）；
    // 清单没有门禁 → 表里不许写（防"假声明"：文档承诺了代码并未兑现的 403）。
    if (route.permission && !cells[3].includes(route.permission)) {
      docDrift.push(`${route.path}：鉴权列缺 ${route.permission}`);
    }
    if (route.requiresAuth && !cells[3].includes("需登录")) docDrift.push(`${route.path}：鉴权列缺「需登录」`);
    if (!route.permission && !route.requiresAuth) {
      if (cells[3].includes("需登录")) docDrift.push(`${route.path}：鉴权列写了「需登录」，但清单未标 requiresAuth`);
      const permLike = cells[3].match(/[a-z_]+:[a-z_]+/);
      if (permLike) docDrift.push(`${route.path}：鉴权列写了权限 ${permLike[0]}，但清单未标 permission`);
    }
  }
  if (docDrift.length === 0) {
    pass(`ARCHITECTURE §6 逐行字段（kind / 页面目录 / 组件 / 去向 / 鉴权）与清单一致（${manifestPathList.length} 条）`);
  } else {
    fail(`ARCHITECTURE §6 逐行字段与清单不一致：\n      ${docDrift.join("\n      ")}`);
  }
}

if (failed) {
  console.log(`\n路由迁移门禁未通过：${failed} 项`);
  process.exit(1);
}
console.log(
  `\n路由迁移门禁通过：旧 15 条全覆盖（${REDIRECTS.length} redirect + ${KEEP.length} 保留），` +
    `${NEW_TARGETS.length} 条新目标已建立，占位路由 ${placeholderPaths.length} 条`
);
