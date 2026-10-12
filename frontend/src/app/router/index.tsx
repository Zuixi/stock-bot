import { Routes, Route, Navigate, useParams } from "react-router-dom";
import { lazy, Suspense } from "react";
import { MainLayout } from "@/app/layouts/MainLayout";
import { RequireAuth } from "@/features/auth";
import { Spin } from "antd";

// ── 既有页面（保留 + 换前缀）────────────────────────────────────────────────
const LandingPage = lazy(() => import("@/pages/landing"));
const LoginPage = lazy(() => import("@/pages/login"));
const MarketPage = lazy(() => import("@/pages/market"));
const CategoryPage = lazy(() => import("@/pages/market-category"));
const MarketHeatmapPage = lazy(() => import("@/pages/market-heatmap"));
const MarketIndexPage = lazy(() => import("@/pages/market-index"));
const IndustryLevel2Page = lazy(() => import("@/pages/market-industry-level2"));
const IndustryLevel3Page = lazy(() => import("@/pages/market-industry-level3"));
const StockDetailPage = lazy(() => import("@/pages/stock-detail"));
const WatchlistPage = lazy(() => import("@/pages/watchlist"));
const TagsPage = lazy(() => import("@/pages/tags"));
const TagsDetailPage = lazy(() => import("@/pages/tags-detail"));

// ── 一级导航页面 ───────────────────────────────────────────────────────────
const MePage = lazy(() => import("@/pages/me"));
const NewsPage = lazy(() => import("@/pages/news"));
const SearchPage = lazy(() => import("@/pages/search"));
const IndustryPage = lazy(() => import("@/pages/industry"));
const IndustryWorkbenchPage = lazy(() => import("@/pages/industry/workbench"));
const CompanyPage = lazy(() => import("@/pages/company"));
const MacroPage = lazy(() => import("@/pages/macro"));
const SentimentPage = lazy(() => import("@/pages/sentiment"));
const ValueGraphPage = lazy(() => import("@/pages/valuegraph"));
const StrategyPage = lazy(() => import("@/pages/strategy"));
const RiskPage = lazy(() => import("@/pages/risk"));
const TradePage = lazy(() => import("@/pages/trade"));

function PageLoading() {
  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 400 }}>
      <Spin size="large" />
    </div>
  );
}

/** 所有页面级路由统一走同一段 Suspense 兜底，避免逐条重复（原实现逐条包 Suspense） */
function withSuspense(node: React.ReactNode) {
  return <Suspense fallback={<PageLoading />}>{node}</Suspense>;
}

/** `/index/:tsCode` → `/market/index/:tsCode`（IA §4 第 9 行；参数名保持 `:tsCode`） */
function LegacyIndexRedirect() {
  const { tsCode } = useParams<{ tsCode: string }>();
  return <Navigate to={`/market/index/${tsCode ?? ""}`} replace />;
}

/**
 * `/tags/:tagName` → `/me?tab=tags&name=:tagName`（IA §4 第 15 行）。
 * `encodeURIComponent` 必须保留：标签名可能含 `/`、`?`、`#` 等字符，
 * 直接拼进 query 会把参数截断（既有 `/tags/:tagName` 的 tagName 是已编码段）。
 */
function LegacyTagRedirect() {
  const { tagName } = useParams<{ tagName: string }>();
  return <Navigate to={`/me?tab=tags&name=${encodeURIComponent(tagName ?? "")}`} replace />;
}

/**
 * 应用路由表。**本文件是 IA 文档 §4「完整路由表（既有 15 条，一条不漏）」的落地**：
 * 10 条 redirect（`<Navigate replace>`，不得静默 404）+ 5 条保留（语义未变）。
 * 逐条对照的机械校验见 `scripts/check-routes.mjs`。
 *
 * 权限三态（IA §1 / ADR 0008，**不新增 scope**）：
 * - 公开（免登录）：`/market/**`、`/company/**`、`/`、`/login`
 * - `research:read`：`/industry`、`/industry/:industryKey`（沿用既有 `RequireAuth permissions`）
 * - 登录即可：`/me`
 * - 待定（后端尚不存在 → 对已登录用户可见、不加门禁）：`/news` `/macro` `/sentiment`
 *   `/valuegraph` `/strategy` `/risk` `/trade` `/search`
 */
export function AppRouter() {
  return (
    <Routes>
      {/* Landing 宣传页：公开路由，独立布局不套 MainLayout（契约 §2）；
          已登录停留此页，由页内 CTA「进入工作台」跳 /market */}
      <Route path="/" element={withSuspense(<LandingPage />)} />

      <Route element={<MainLayout />}>
        <Route path="/login" element={withSuspense(<LoginPage />)} />

        {/* ── 市场 `/market`（公开，含全部子路由）────────────────────────── */}
        <Route path="/market" element={withSuspense(<MarketPage />)} />
        {/* `/market/category` → 此处（IA §4 第 4 行） */}
        <Route path="/market/industry" element={withSuspense(<CategoryPage />)} />
        <Route path="/market/industry/:level1Code" element={withSuspense(<IndustryLevel2Page />)} />
        <Route
          path="/market/industry/:level1Code/:level2Code"
          element={withSuspense(<IndustryLevel3Page />)}
        />
        {/* `/market/hot-sectors/:category` 与 `/market/concept/:boardCode` 共用此目标页 */}
        <Route path="/market/heatmap" element={withSuspense(<MarketHeatmapPage />)} />
        {/* `/index/:tsCode` → 此处（参数名保持 :tsCode） */}
        <Route path="/market/index/:tsCode" element={withSuspense(<MarketIndexPage />)} />

        {/* ── 公司分析 `/company`（公开；吸收既有 `/stock/:symbol`）───────── */}
        <Route path="/company" element={withSuspense(<CompanyPage />)} />
        <Route path="/company/:symbol" element={withSuspense(<StockDetailPage />)} />

        {/* ── 行业分析 `/industry`（`research:read`，吸收既有 `/research`）──
            守卫只做体验（重定向 / 403 页），真实门禁在后端 /api/v1/industries 的
            router 级权限依赖（docs/decisions/0008）；redirect 不放宽权限 */}
        <Route
          path="/industry"
          element={withSuspense(
            <RequireAuth permissions={["research:read"]}>
              <IndustryPage />
            </RequireAuth>
          )}
        />
        <Route
          path="/industry/:industryKey"
          element={withSuspense(
            <RequireAuth permissions={["research:read"]}>
              <IndustryWorkbenchPage />
            </RequireAuth>
          )}
        />

        {/* ── 待定权限的一级导航（后端尚不存在 → 登录可见、无门禁）───────── */}
        <Route path="/news" element={withSuspense(<NewsPage />)} />
        <Route path="/macro" element={withSuspense(<MacroPage />)} />
        <Route path="/sentiment" element={withSuspense(<SentimentPage />)} />
        <Route path="/valuegraph" element={withSuspense(<ValueGraphPage />)} />
        <Route path="/strategy" element={withSuspense(<StrategyPage />)} />
        <Route path="/risk" element={withSuspense(<RiskPage />)} />
        <Route path="/trade" element={withSuspense(<TradePage />)} />
        {/* 顶栏全局搜索的目标页（非 redirect；`?q=` 可见性待定） */}
        <Route path="/search" element={withSuspense(<SearchPage />)} />

        {/* ── 我的 `/me`（登录即可，沿用既有 RequireAuth 无权限参数）─────── */}
        <Route
          path="/me"
          element={withSuspense(
            <RequireAuth>
              <MePage />
            </RequireAuth>
          )}
        />

        {/* ══ 旧路由 10 条 redirect（IA §4；目标必须逐字一致）══════════════ */}
        {/* 1-3 市场三兄弟（处理后仍公开） */}
        <Route path="/market/category" element={<Navigate to="/market/industry" replace />} />
        <Route
          path="/market/hot-sectors/:category"
          element={<LegacyMarketHotSectorsRedirect />}
        />
        <Route path="/market/concept/:boardCode" element={<LegacyMarketConceptRedirect />} />
        {/* 4-5 指数 / 个股（处理后仍公开） */}
        <Route path="/index/:tsCode" element={<LegacyIndexRedirect />} />
        <Route path="/stock/:symbol" element={<LegacyStockRedirect />} />
        {/* 6-7 投研（必须继续兑现 research:read，redirect 不得放宽为"登录即可"） */}
        <Route path="/research" element={<Navigate to="/industry" replace />} />
        <Route path="/research/:industryKey" element={<LegacyResearchRedirect />} />
        {/* 8-10 自选 / 标签（登录即可，与现状相同） */}
        <Route path="/watchlist" element={<Navigate to="/me?tab=watchlist" replace />} />
        <Route path="/tags" element={<Navigate to="/me?tab=tags" replace />} />
        <Route path="/tags/:tagName" element={<LegacyTagRedirect />} />

        {/* ══ 5 条保留（语义未变）═══════════════════════════════════════════
            `/market` / `/market/industry/:level1Code` / `/market/industry/:level1Code/:level2Code`
            已在上面按新前缀声明；`/`（landing）与 `/login` 同样保留。 */}
      </Route>
    </Routes>
  );
}

/** `/market/hot-sectors/:category` → `/market/heatmap?group=:category`（IA §4 第 7 行） */
function LegacyMarketHotSectorsRedirect() {
  const { category } = useParams<{ category: string }>();
  return <Navigate to={`/market/heatmap?group=${encodeURIComponent(category ?? "")}`} replace />;
}

/** `/market/concept/:boardCode` → `/market/heatmap?concept=:boardCode`（IA §4 第 8 行） */
function LegacyMarketConceptRedirect() {
  const { boardCode } = useParams<{ boardCode: string }>();
  return <Navigate to={`/market/heatmap?concept=${encodeURIComponent(boardCode ?? "")}`} replace />;
}

/** `/stock/:symbol` → `/company/:symbol`（IA §4 第 10 行） */
function LegacyStockRedirect() {
  const { symbol } = useParams<{ symbol: string }>();
  return <Navigate to={`/company/${symbol ?? ""}`} replace />;
}

/** `/research/:industryKey` → `/industry/:industryKey`（IA §4 第 12 行） */
function LegacyResearchRedirect() {
  const { industryKey } = useParams<{ industryKey: string }>();
  return <Navigate to={`/industry/${industryKey ?? ""}`} replace />;
}
