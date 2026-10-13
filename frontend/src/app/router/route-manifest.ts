// 路由清单（**唯一真相**）：`router/index.tsx` 由它生成 `<Route>`，`scripts/check-routes.mjs` 由它取断言数据。
//
// 为什么要有这个文件（Ruling 33）：此前 check-routes.mjs 靠**解析 router 的 JSX 源码**做门禁，
// 两轮里连续假绿——第一轮把占位判定建立在"页面 import 了哪个组件"上、且只从已知 redirect 出发数
// （新增 `<Navigate>` 被静默过滤）；第二轮把判定收敛成"页面自己的文件里有标记"（对共享
// `PlaceholderPage` 的 9 条路由全部失效），扫描用 `indexOf("<Route")` 又从 `<Routes>` 与无 path 的
// layout 标签上"吞"掉 `/` 与 `/login`。**解析源码这条路本身不可靠，故把清单抽成数据**：
// 路由与门禁读同一份，不再有"JSX 解析"这一层。
//
// **本文件是纯数据**（不 import React 或任何组件：`check-routes.mjs` 由 node 直接 import 它），
// React 侧（懒加载注册表与元素渲染）在 `route-elements.tsx`。
//
// 维护契约：模块真页面落地时，把对应条目的 `isPlaceholder: true` 去掉，并同步 `route-elements.tsx` 的
// `PLACEHOLDER_NOTICE` 文案与 `check-routes.mjs` 的 `PLACEHOLDER_CONTRACT`（占位集合的成员与基数都是
// 被断言的契约）——门禁还会断言"任何 redirect 的目标不得是 `isPlaceholder` 路由"。

/**
 * 顶栏 10 个一级导航（顺序 = IA 文档 §1 的表序，逐字）。
 * 可见性三态照抄 IA §1（口径来源 ADR 0008），**不新增任何 scope**。
 * `MainLayout` 与门禁都读这里，避免"顶栏删了一个导航但没人发现"。
 */
export const NAV_ROOTS = [
  { path: "/market", title: "市场" },
  { path: "/news", title: "资讯" },
  { path: "/industry", title: "行业分析" },
  { path: "/company", title: "公司分析" },
  { path: "/macro", title: "宏观分析" },
  { path: "/sentiment", title: "情绪分析" },
  { path: "/valuegraph", title: "价值网图谱" },
  { path: "/strategy", title: "策略引擎" },
  { path: "/risk", title: "风险管理" },
  { path: "/trade", title: "交易执行" },
] as const;

/** 权限可见性三态（口径见 IA 文档 §1 与 ADR 0008） */
export type NavVisibility =
  /** 公开：未登录也可见 */
  | { kind: "public" }
  /** 登录即可：未登录不可见 */
  | { kind: "authenticated" }
  /** 需具体权限：无权者不可见（权限名沿用既有口径，本层不新增 scope） */
  | { kind: "permission"; permission: string };

export type RouteKind = "page" | "redirect" | "layout";

export interface AppRoute {
  /** 稳定标识（也是 React Router 的 key）；layout 路由用它做 `parent` 的引用目标 */
  key: string;
  path: string;
  /**
   * - `page`     —— 渲染一个页面（`component` 必填，路由级可再套 `RequireAuth` / 占位壳）
   * - `redirect` —— `<Navigate to={redirectTo} replace />`
   * - `layout`   —— 只做 `<Outlet />` 外壳（当前只有 MainLayout 一条，`path` 为空串）
   */
  kind: RouteKind;
  /** `kind === "page"`：`route-elements.tsx` 的 `PAGES` 注册表键 */
  component?: string;
  /** `kind === "redirect"`：目标（可含 `:param` 与 query） */
  redirectTo?: string;
  /** 该路由今天只是「规划中」占位页（门禁据此断言"redirect 目标不得是占位页"） */
  isPlaceholder?: boolean;
  /** 需登录（沿用既有 `RequireAuth`，无具体权限参数） */
  requiresAuth?: boolean;
  /** 需具体权限（沿用既有 `RequireAuth permissions={[...]}`；权限名不得新增，见 IA §1） */
  permission?: string;
  /**
   * 父级 `layout` 路由的键（`AppRoute.key`）。空/缺省 = 顶层。
   * 与改造前的嵌套结构逐条等价：landing 在顶层（不套 `MainLayout`），其余全部挂在 `main` 下。
   */
  parent?: string;
}

/**
 * 全部路由。既有 **15 条一条不漏**（Ruling 30 后 = 8 redirect + 7 保留）。
 * 顺序即 React Router 的声明顺序；页面路由一律经 `Suspense` + `lazy` 渲染（与改造前逐条等价）。
 */
export const ROUTES: AppRoute[] = [
  // ── 顶层：landing（公开，独立布局，不套 MainLayout）────────────────────────
  { key: "landing", path: "/", kind: "page", component: "LandingPage" },
  // ── 顶层：MainLayout 外壳（唯一 layout 路由，无 path）─────────────────────
  { key: "main", path: "", kind: "layout" },

  // ── 以下全部 `parent: "main"`（与改造前的嵌套结构逐条等价）────────────────

  // 登录页（公开）
  { key: "login", path: "/login", kind: "page", component: "LoginPage", parent: "main" },

  // 市场 `/market`（公开，含全部子路由）
  { key: "market", path: "/market", kind: "page", component: "MarketPage", parent: "main" },
  // S1.3：全球市场与跨市场标的（公开；段 B 大宗/外汇只放「数据源待接入」占位）
  {
    key: "market-global",
    path: "/market/global",
    kind: "page",
    component: "MarketGlobalPage",
    parent: "main",
  },
  { key: "market-industry", path: "/market/industry", kind: "page", component: "CategoryPage", parent: "main" },
  {
    key: "market-industry-l2",
    path: "/market/industry/:level1Code",
    kind: "page",
    component: "IndustryLevel2Page",
    parent: "main",
  },
  {
    key: "market-industry-l3",
    path: "/market/industry/:level1Code/:level2Code",
    kind: "page",
    component: "IndustryLevel3Page",
    parent: "main",
  },
  // S1.4 新增：末级成分股页（`/market/industry/:l1/:l2/:l3`）。
  // 为什么是**新路由**而不是把末级塞进上面那条：spec §S1.4 的钻取路径是
  // 「31 一级 → 二级 → 三级 → 个股」，末级语义是"某个三级行业的成分股 + 该节点 7 项聚合"，
  // 与"某二级下的三级清单 + 可切子集"是两件事；二/三级两条既有保留路由语义**未变**
  // （Ruling 30：不得让既有入口指向占位页），故原地保留、末级另开一条。
  // 权限：`/market/**` 公开免登录（ADR 0008）——本行**不加** requiresAuth / permission。
  {
    key: "market-industry-l4",
    path: "/market/industry/:level1Code/:level2Code/:level3Code",
    kind: "page",
    component: "IndustryLevel4Page",
    parent: "main",
  },
  // `/market/heatmap`：**不是任何 redirect 的目标**（Ruling 30）；S1.5 已兑现真实现
  // （treemap 热力图，消费批量读路 `/api/v1/market/heatmap/treemap`），`isPlaceholder`
  // 已摘除——`check-routes.mjs` 的占位契约与定点断言同步改写（维护契约见本文件头）。
  {
    key: "market-heatmap",
    path: "/market/heatmap",
    kind: "page",
    component: "MarketHeatmapPage",
    parent: "main",
  },
  // S1.6 新增：A股排行 7 榜（市值/股息率/月度涨幅/月度跌幅/半年利润/年利润/ROE）。
  // 为什么带 `:key` 而不是拆成 7 条路由：7 个榜**共用同一个页面壳与同一套口径披露**
  // （Tab 切换），拆成 7 条会让"新增第 8 个榜"变成改三处清单 + 文档。
  // 为什么新开一条而不是改 `/market`：既有 `/market` 的榜单矩阵消费的是 5 个**单日**榜
  // （`/api/v1/market/rankings?type=`），与 7 榜的来源/口径都不同，不得互相顶替（Ruling 30）。
  // 权限：`/market/**` 公开免登录（ADR 0008）——本行**不加** requiresAuth / permission。
  {
    key: "market-rankings",
    path: "/market/rankings/:key",
    kind: "page",
    component: "MarketRankingsPage",
    parent: "main",
  },
  { key: "market-index", path: "/market/index/:tsCode", kind: "page", component: "MarketIndexPage", parent: "main" },
  // 以下两条是**保留**路由：热点板块 / 概念板块今天就是有数据的真页面（Ruling 30）
  {
    key: "market-hot-sectors",
    path: "/market/hot-sectors/:category",
    kind: "page",
    component: "MarketHotSectorsPage",
    parent: "main",
  },
  {
    key: "market-concept",
    path: "/market/concept/:boardCode",
    kind: "page",
    component: "ConceptBoardPage",
    parent: "main",
  },

  // 公司分析 `/company`（公开；吸收既有 `/stock/:symbol`）
  { key: "company", path: "/company", kind: "page", component: "CompanyPage", isPlaceholder: true, parent: "main" },
  { key: "company-symbol", path: "/company/:symbol", kind: "page", component: "StockDetailPage", parent: "main" },

  // 行业分析 `/industry`（`research:read`，吸收既有 `/research`）
  {
    key: "industry",
    path: "/industry",
    kind: "page",
    component: "IndustryPage",
    permission: "research:read",
    parent: "main",
  },
  {
    key: "industry-workbench",
    path: "/industry/:industryKey",
    kind: "page",
    component: "IndustryWorkbenchPage",
    permission: "research:read",
    parent: "main",
  },

  // S1.8–S1.13 已兑现真页面：`/sentiment` 从占位转为真页面。守卫必须与**数据需求一致**：
  // 页面消费 `/api/v1/metrics/*`，而该读面要求 `research:read`（实测：匿名 401、普通交易员 403），
  // 故与同为研究层的 `/industry` 用**同一个** `permission`，而不是只要求登录——否则普通登录用户
  // 能进页面却读不到任何模块数据（实测过，页面只能显示"读面未取到"的空表）。
  // IA §1 的权限列原为「待定」；该表的「分区」列本就写着情绪分析 = 研究（只读），本行据此落地。
  {
    key: "sentiment",
    path: "/sentiment",
    kind: "page",
    component: "SentimentPage",
    permission: "research:read",
    parent: "main",
  },

  // 待定权限的一级导航（后端尚不存在 → 登录可见、无门禁）
  { key: "news", path: "/news", kind: "page", component: "NewsPage", isPlaceholder: true, parent: "main" },
  { key: "macro", path: "/macro", kind: "page", component: "MacroPage", isPlaceholder: true, parent: "main" },
  {
    key: "valuegraph",
    path: "/valuegraph",
    kind: "page",
    component: "ValueGraphPage",
    isPlaceholder: true,
    parent: "main",
  },
  { key: "strategy", path: "/strategy", kind: "page", component: "StrategyPage", isPlaceholder: true, parent: "main" },
  { key: "risk", path: "/risk", kind: "page", component: "RiskPage", isPlaceholder: true, parent: "main" },
  { key: "trade", path: "/trade", kind: "page", component: "TradePage", isPlaceholder: true, parent: "main" },
  // 顶栏全局搜索的目标页（非 redirect；`?q=` 可见性待定）
  { key: "search", path: "/search", kind: "page", component: "SearchPage", isPlaceholder: true, parent: "main" },

  // 我的 `/me`（登录即可，沿用既有 `RequireAuth` 无权限参数）
  { key: "me", path: "/me", kind: "page", component: "MePage", requiresAuth: true, parent: "main" },

  // 旧路由 8 条 redirect（IA §4 + Ruling 30；目标必须逐字一致）
  { key: "legacy-market-category", path: "/market/category", kind: "redirect", redirectTo: "/market/industry", parent: "main" },
  { key: "legacy-index", path: "/index/:tsCode", kind: "redirect", redirectTo: "/market/index/:tsCode", parent: "main" },
  { key: "legacy-stock", path: "/stock/:symbol", kind: "redirect", redirectTo: "/company/:symbol", parent: "main" },
  { key: "legacy-research", path: "/research", kind: "redirect", redirectTo: "/industry", parent: "main" },
  {
    key: "legacy-research-industry",
    path: "/research/:industryKey",
    kind: "redirect",
    redirectTo: "/industry/:industryKey",
    parent: "main",
  },
  { key: "legacy-watchlist", path: "/watchlist", kind: "redirect", redirectTo: "/me?tab=watchlist", parent: "main" },
  { key: "legacy-tags", path: "/tags", kind: "redirect", redirectTo: "/me?tab=tags", parent: "main" },
  // `/tags/:tagName` **是 redirect**（目标 `/me?tab=tags&name=`，参数由 RedirectTo 转义）
  {
    key: "legacy-tag-detail",
    path: "/tags/:tagName",
    kind: "redirect",
    redirectTo: "/me?tab=tags&name=:tagName",
    parent: "main",
  },
];

/** 便捷派生（门禁与文档核对共用；一律用函数，避免循环依赖与求值顺序坑） */
export const getPageRoutes = () => ROUTES.filter((r) => r.kind === "page");
export const getRedirectRoutes = () => ROUTES.filter((r) => r.kind === "redirect");
export const getPlaceholderRoutes = () => ROUTES.filter((r) => r.isPlaceholder);
export const getRoutePaths = () => ROUTES.filter((r) => r.path).map((r) => r.path);
