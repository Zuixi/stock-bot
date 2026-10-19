import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, useLocation } from "react-router-dom";
import { AppRouter } from "@/app/router";
import { routePatternKey } from "@/app/router/routeKey";
import { buildAntdTheme } from "@/app/theme";
import { ThemeProvider, useTheme } from "@/app/theme-context";
import { AuthProvider } from "@/features/auth";
import { ApiError } from "@/shared/api/client";
import { ErrorBoundary } from "@/shared/ui/ErrorBoundary";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: (failureCount, error) => {
        // Never retry on 401 unauthorized or 403 forbidden
        if (error instanceof ApiError && (error.status === 401 || error.status === 403)) {
          return false;
        }
        return failureCount < 1;
      },
      refetchOnWindowFocus: false,
    },
  },
});

/**
 * 渲染异常兜底必须随**页面**重置：`ErrorBoundary` 一旦进入错误态就永久渲染错误卡，
 * 用户按浏览器返回键只会看到**同一张错误页**（"返回键失效"的第二种成因）。
 *
 * key 用**路由模式**而非具体 `pathname`（2026-10-18 修正）：参数化路由的 pathname 会随
 * 参数变化（`/market/sentiment/zt_count` → `/market/sentiment/broken_rate`），原先的
 * `key={pathname}` 会把整棵树（含 MainLayout）卸载重挂 —— 切换指标时整页闪骨架、丢滚动位置。
 * 归一成 `/market/sentiment/:metric` 后，同页换参数不重置；真正换页仍会重置错误态。
 * 也不用 `location.key`：那会让 `?tab=` 这类查询参数变化触发同样的整树重挂。
 */
function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={routePatternKey(pathname)}>{children}</ErrorBoundary>;
}

/** ConfigProvider 需要消费 ThemeContext，必须在 ThemeProvider 内层渲染 */
function RootProviders() {
  const { mode } = useTheme();
  return (
    <ConfigProvider locale={zhCN} theme={buildAntdTheme(mode)}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <RouteErrorBoundary>
              <AppRouter />
            </RouteErrorBoundary>
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ConfigProvider>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <RootProviders />
    </ThemeProvider>
  );
}
