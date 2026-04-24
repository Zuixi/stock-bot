import { Routes, Route, Navigate } from "react-router-dom";
import { lazy, Suspense } from "react";
import { MainLayout } from "@/app/layouts/MainLayout";
import { Spin } from "antd";

const MarketPage = lazy(() => import("@/pages/market"));
const CategoryPage = lazy(() => import("@/pages/market-category"));
const IndustryLevel2Page = lazy(() => import("@/pages/market-industry-level2"));
const IndustryLevel3Page = lazy(() => import("@/pages/market-industry-level3"));
const MarketHotSectorsPage = lazy(() => import("@/pages/market-hot-sectors"));
const IndexDetailPage = lazy(() => import("@/pages/index-detail"));
const StockDetailPage = lazy(() => import("@/pages/stock-detail"));
const WatchlistPage = lazy(() => import("@/pages/watchlist"));
const TagsPage = lazy(() => import("@/pages/tags"));
const TagsDetailPage = lazy(() => import("@/pages/tags-detail"));

function PageLoading() {
  return (
    <div style={{ display: "flex", justifyContent: "center", alignItems: "center", height: 400 }}>
      <Spin size="large" />
    </div>
  );
}

export function AppRouter() {
  return (
    <Routes>
      <Route element={<MainLayout />}>
        <Route path="/" element={<Navigate to="/market" replace />} />
        <Route
          path="/market"
          element={
            <Suspense fallback={<PageLoading />}>
              <MarketPage />
            </Suspense>
          }
        />
        <Route
          path="/market/category"
          element={
            <Suspense fallback={<PageLoading />}>
              <CategoryPage />
            </Suspense>
          }
        />
        <Route
          path="/market/industry/:level1Code"
          element={
            <Suspense fallback={<PageLoading />}>
              <IndustryLevel2Page />
            </Suspense>
          }
        />
        <Route
          path="/market/industry/:level1Code/:level2Code"
          element={
            <Suspense fallback={<PageLoading />}>
              <IndustryLevel3Page />
            </Suspense>
          }
        />
        <Route
          path="/market/hot-sectors/:category"
          element={
            <Suspense fallback={<PageLoading />}>
              <MarketHotSectorsPage />
            </Suspense>
          }
        />
        <Route
          path="/index/:tsCode"
          element={
            <Suspense fallback={<PageLoading />}>
              <IndexDetailPage />
            </Suspense>
          }
        />
        <Route
          path="/stock/:symbol"
          element={
            <Suspense fallback={<PageLoading />}>
              <StockDetailPage />
            </Suspense>
          }
        />
        <Route
          path="/watchlist"
          element={
            <Suspense fallback={<PageLoading />}>
              <WatchlistPage />
            </Suspense>
          }
        />
        <Route
          path="/tags"
          element={
            <Suspense fallback={<PageLoading />}>
              <TagsPage />
            </Suspense>
          }
        />
        <Route
          path="/tags/:tagName"
          element={
            <Suspense fallback={<PageLoading />}>
              <TagsDetailPage />
            </Suspense>
          }
        />
      </Route>
    </Routes>
  );
}
