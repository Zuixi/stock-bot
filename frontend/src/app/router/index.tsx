import { Routes, Route, Navigate } from "react-router-dom";
import { lazy, Suspense } from "react";
import { MainLayout } from "@/app/layouts/MainLayout";
import { Spin } from "antd";

const MarketPage = lazy(() => import("@/pages/market"));
const CategoryPage = lazy(() => import("@/pages/market-category"));
const StockDetailPage = lazy(() => import("@/pages/stock-detail"));
const WatchlistPage = lazy(() => import("@/pages/watchlist"));

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
      </Route>
    </Routes>
  );
}
