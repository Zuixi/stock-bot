import { lazy, Suspense, type ReactNode } from "react";
import { Navigate, useParams } from "react-router-dom";
import { Spin } from "antd";
import { MainLayout } from "@/app/layouts/MainLayout";
import { RequireAuth } from "@/features/auth";
import { PlaceholderNotice } from "@/shared/ui/PlaceholderNotice";
import { type AppRoute } from "./route-manifest";

/**
 * 路由清单的 React 侧：懒加载注册表 + 元素渲染。
 *
 * `router/index.tsx` 由 `ROUTES`（纯数据）**生成** `<Route>`，本文件负责"一条路由渲染成什么元素"。
 * 好处：门禁不再解析 JSX（Ruling 33），而渲染行为与改造前逐条等价（Suspense / lazy / RequireAuth）。
 */

// ── 页面懒加载注册表（键 = `route-manifest.ts` 里 `component` 的取值）────────────
const PAGES: Record<string, React.LazyExoticComponent<React.ComponentType>> = {
  LandingPage: lazy(() => import("@/pages/landing")),
  LoginPage: lazy(() => import("@/pages/login")),
  MarketPage: lazy(() => import("@/pages/market")),
  CategoryPage: lazy(() => import("@/pages/market-category")),
  MarketHeatmapPage: lazy(() => import("@/pages/market-heatmap")),
  MarketIndexPage: lazy(() => import("@/pages/market-index")),
  IndustryLevel2Page: lazy(() => import("@/pages/market-industry-level2")),
  IndustryLevel3Page: lazy(() => import("@/pages/market-industry-level3")),
  // 保留路由（Ruling 30）：今天就有数据的真页面，不 redirect 到占位页
  MarketHotSectorsPage: lazy(() => import("@/pages/market-hot-sectors")),
  ConceptBoardPage: lazy(() => import("@/pages/market-concept")),
  // 注：`/tags/:tagName` 是 **redirect**（→ `/me?tab=tags&name=`），故没有 `TagsDetailPage` 注册项；
  // 标签个股列表由 `TagsPanel` 渲染（`pages/tags-detail` 已按 Ruling 33 删除：无人渲染 = 死代码）
  StockDetailPage: lazy(() => import("@/pages/stock-detail")),
  MePage: lazy(() => import("@/pages/me")),
  IndustryPage: lazy(() => import("@/pages/industry")),
  IndustryWorkbenchPage: lazy(() => import("@/pages/industry/workbench")),
  // 占位模块（`isPlaceholder: true`）：页面落地时把 `route-manifest.ts` 的标记去掉、并把这里的 import 换成真页面
  NewsPage: lazy(() => import("@/pages/news")),
  SearchPage: lazy(() => import("@/pages/search")),
  CompanyPage: lazy(() => import("@/pages/company")),
  MacroPage: lazy(() => import("@/pages/macro")),
  SentimentPage: lazy(() => import("@/pages/sentiment")),
  ValueGraphPage: lazy(() => import("@/pages/valuegraph")),
  StrategyPage: lazy(() => import("@/pages/strategy")),
  RiskPage: lazy(() => import("@/pages/risk")),
  TradePage: lazy(() => import("@/pages/trade")),
};

/**
 * 占位路由的通告条——**由路由注入**，不依赖页面文件里有没有某个 import/字符串。
 * `route-manifest.ts` 里 `isPlaceholder: true` 的页面，其下一定渲染本组件（可机械验证 testid）。
 */
const PLACEHOLDER_NOTICE: Record<string, { title: string; hint?: string }> = {
  "/market/heatmap": { title: "市场热力图" },
  "/company": {
    title: "公司分析",
    hint: "9 张公司卡片尚未落地；本页为占位路由，原 /stock/:symbol 已 redirect 到此",
  },
  "/news": { title: "资讯" },
  "/macro": { title: "宏观分析" },
  "/sentiment": { title: "情绪分析" },
  "/valuegraph": { title: "价值网图谱" },
  "/strategy": { title: "策略引擎" },
  "/risk": { title: "风险管理" },
  "/trade": { title: "交易执行" },
  "/search": {
    title: "搜索",
    hint: "顶栏全局搜索的目标页（跨股票 / 行业 / 指数 / 概念 / 资讯），可见性待定；本任务只保证 ?q= 可达、不 404",
  },
};

function PageLoading() {
  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 400 }}>
      <Spin size="large" />
    </div>
  );
}

/** 所有页面级路由统一走同一段 Suspense 兜底（与改造前逐条包 Suspense 等价） */
function withSuspense(node: ReactNode) {
  return <Suspense fallback={<PageLoading />}>{node}</Suspense>;
}

/** 用来源参数填充 redirect 目标里的 `:param`（`redirectTo` 是契约写法，这里转成实际 URL） */
function RedirectTo({ route }: { route: AppRoute }) {
  const params = useParams();
  const target = (route.redirectTo ?? "").replace(/:(\w+)/g, (_, name: string) => {
    const value = params[name] ?? "";
    return name === "tagName" ? encodeURIComponent(value) : value;
  });
  return <Navigate to={target} replace />;
}

/** 把清单里的一条 `page` 路由渲染成元素（等价于改造前手写的逐条 JSX） */
export function renderPageRoute(route: AppRoute): ReactNode {
  const Component = route.component ? PAGES[route.component] : undefined;
  if (!Component) {
    throw new Error(`路由清单里的 component 未注册到 PAGES：${route.path} → ${route.component}`);
  }

  const notice = route.isPlaceholder ? PLACEHOLDER_NOTICE[route.path] : undefined;
  if (route.isPlaceholder && !notice) {
    throw new Error(`占位路由缺少 PLACEHOLDER_NOTICE 文案：${route.path}`);
  }

  const page = (
    <>
      {notice ? <PlaceholderNotice title={notice.title} hint={notice.hint} /> : null}
      <Component />
    </>
  );

  if (route.permission) {
    return withSuspense(<RequireAuth permissions={[route.permission]}>{page}</RequireAuth>);
  }
  if (route.requiresAuth) {
    return withSuspense(<RequireAuth>{page}</RequireAuth>);
  }
  return withSuspense(page);
}

/**
 * 把清单里的一条 `redirect` 路由渲染成元素。
 * `RedirectTo` 统一用来源参数填充目标里的 `:param`——**一条实现覆盖全部 redirect**，
 * 不需要"每种 redirect 写一个组件"（那正是注释与实现容易漂移的地方）。
 */
export function renderRedirectRoute(route: AppRoute): ReactNode {
  return <RedirectTo route={route} />;
}

export function renderRoute(route: AppRoute): ReactNode {
  switch (route.kind) {
    case "layout":
      return <MainLayout />;
    case "redirect":
      return renderRedirectRoute(route);
    case "page":
      return renderPageRoute(route);
  }
}

// 注：这里曾经导出 `PAGE_COMPONENT_KEYS` / `PLACEHOLDER_PATHS` / `ROUTES` 供"门禁/自检核对"，
// 但 `check-routes.mjs` 由 node 直接 import 纯数据文件；本文件是 `.tsx`（JSX + React/antd import），
// node 的 type stripping **加载不了它**，所以那三个导出从来没有消费者 → 已按 Ruling 33 的同一理由删除。
// 门禁对本文件的漂移探针是**源码正则**（`check-routes.mjs` 的"占位保护面"一节）：`PAGES` 的注册键、
// `lazy(() => import("@/pages/<dir>"))` 的页面目录、`PLACEHOLDER_NOTICE` 的文案键——解析不到时会响亮报错，不静默跳过。
