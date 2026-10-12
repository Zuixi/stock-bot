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
//   7. 站内链接不得再写已迁移的旧裸路径；不含 `/calendar`；
//   8. **IA §4 文档 ↔ 清单契约 双向比对**（表行成员、净效果块分组合计与成员数、合计式、契约段声明数字）。

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

/** 允许出现的额外路由（既有页面按新前缀搬迁 / 目标带参变体）：超出即报错，防私加路由 */
const ALLOWED_EXTRA = [
  ...NAV_ROOTS.map((n) => n.path),
  "/market/index/:tsCode",
  "/company/:symbol",
  "/industry/:industryKey",
  "/me",
  "/search",
  "/market/industry",
  // `/market/heatmap` 仍是**已声明路由**（S1.5 建真热力图），但它不是 redirect 目标
  "/market/heatmap",
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
const notUnderLayout = declaredRoutes.filter(
  (r) => r.kind !== "layout" && r.path !== "/" && r.parent !== "main"
);
if (notUnderLayout.length === 0) {
  pass(`除以 \`/\`（landing 独立布局）外全部路由都嵌在 MainLayout 下（${declaredPaths.length - 2} 条）`);
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
const contract = new Set([...legacy, ...NEW_TARGETS, ...ALLOWED_EXTRA]);
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
const placeholderPaths = getPlaceholderRoutes().map((r) => r.path);
if (placeholderPaths.length === 0) {
  fail("占位路由集合为空（清单里没有任何 isPlaceholder 路由，契约要求至少 10 个占位页）");
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
const elementsSource = readFileSync(join(root, "src/app/router/route-elements.tsx"), "utf8");
const noticeKeys = [...elementsSource.matchAll(/^\s*"([^"]+)":\s*\{/gm)].map((m) => m[1]);
const missingNotice = placeholderPaths.filter((p) => !noticeKeys.includes(p));
if (missingNotice.length === 0) {
  pass(`占位通告映射覆盖全部占位路由（${placeholderPaths.length} 条）`);
} else {
  fail(`占位路由缺少 PLACEHOLDER_NOTICE 文案：${missingNotice.join("、")}`);
}
const pageComponentKeys = new Set(
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

if (failed) {
  console.log(`\n路由迁移门禁未通过：${failed} 项`);
  process.exit(1);
}
console.log(
  `\n路由迁移门禁通过：旧 15 条全覆盖（${REDIRECTS.length} redirect + ${KEEP.length} 保留），` +
    `${NEW_TARGETS.length} 条新目标已建立，占位路由 ${placeholderPaths.length} 条`
);
