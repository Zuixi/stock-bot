import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
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

/** ConfigProvider 需要消费 ThemeContext，必须在 ThemeProvider 内层渲染 */
function RootProviders() {
  const { mode } = useTheme();
  return (
    <ConfigProvider locale={zhCN} theme={buildAntdTheme(mode)}>
      <QueryClientProvider client={queryClient}>
        <AuthProvider>
          <BrowserRouter>
            <ErrorBoundary>
              <AppRouter />
            </ErrorBoundary>
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
