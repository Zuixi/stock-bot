import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import type { ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, useLocation } from "react-router-dom";
import { AppRouter } from "@/app/router";
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
 * 渲染异常兜底必须随路由重置：`ErrorBoundary` 一旦进入错误态就永久渲染错误卡，
 * 用户按浏览器返回键只会看到**同一张错误页**（"返回键失效"的第二种成因）。
 * key 用 `pathname` 而非 `location.key` —— 后者会让 `?tab=` 这类查询参数变化也整树重挂。
 */
function RouteErrorBoundary({ children }: { children: ReactNode }) {
  const { pathname } = useLocation();
  return <ErrorBoundary key={pathname}>{children}</ErrorBoundary>;
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
