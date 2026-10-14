import React, { createContext, useContext, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getSession, logout as logoutApi, type UserProfile } from "@/shared/api/auth";
import { ApiError } from "@/shared/api/client";
import { useWatchlistStore } from "@/features/watchlist/store";
import { useAuthStore } from "./store";

/**
 * 会话探测是**三态**，不是布尔：
 *
 *   authenticated    探测成功，拿到用户
 *   unauthenticated  探测成功但明确未登录（401）
 *   unknown          探测失败（429 / 5xx / 网络抖动）——登录态未知
 *
 * 只有 401 才能降级为"已登出"。把暂时性故障也当成未登录，会把一次网络抖动
 * 画成"被踢下线"，并且丢掉 React Query 自带的"失败保留上次成功数据"保护。
 */
const SESSION_RETRY_LIMIT = 2;

interface AuthContextValue {
  user: UserProfile | null;
  isAuthenticated: boolean;
  isAuthReady: boolean;
  isLoading: boolean;
  /** 会话探测因暂时性故障失败（非 401）：登录态未知，上一次已知用户仍被保留 */
  sessionError: Error | null;
  retrySession: () => void;
  logout: () => Promise<void>;
  refetchUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const { user, isAuthenticated, isAuthReady, setUser, clearAuth, setAuthReady } = useAuthStore();

  const { data, isLoading, isError, error, refetch } = useQuery<UserProfile | null>({
    queryKey: ["auth", "session"],
    queryFn: async () => {
      try {
        // skipAuth: 401 由下面显式分类处理，不再触发全局 unauthorized 事件
        return await getSession({ skipAuth: true });
      } catch (err) {
        // 确定未登录 → 当作一次成功的 null 结果
        if (err instanceof ApiError && err.status === 401) return null;
        // 其余（429/5xx/网络）必须抛出：React Query 会保留上一次成功的 data，
        // 并在下面按退避策略重试
        throw err;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: (failureCount, err) => {
      const status = err instanceof ApiError ? err.status : undefined;
      // 401 是确定结论，重试没有意义；4xx 里的 403 同理（权限问题不是抖动）
      if (status !== undefined && status >= 400 && status < 500) return false;
      return failureCount < SESSION_RETRY_LIMIT;
    },
    retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    if (isLoading) return;
    if (data !== undefined) {
      // 拿到了确定结论：用户对象，或确定的未登录（null）
      setUser(data);
    } else if (isError) {
      // 探测失败且从无成功结果：不清空登录态，只标记"探测已结束"，
      // 让 RequireAuth 渲染可重试的提示而不是无限转圈或误跳登录页
      setAuthReady(true);
    }
  }, [data, isLoading, isError, setUser, setAuthReady]);

  const handleLogout = useCallback(async () => {
    try {
      await logoutApi({ skipAuth: true });
    } catch {
      // Ignore logout errors
    } finally {
      clearAuth();
      useWatchlistStore.getState().clear();
      queryClient.clear();
    }
  }, [clearAuth, queryClient]);

  const refetchUser = useCallback(async () => {
    const res = await refetch();
    // 只在拿到确定结论时改状态；失败保留当前登录态
    // （刚登录完就被一次抖动踢下线是最典型的误伤）
    if (res.isSuccess) setUser(res.data ?? null);
  }, [refetch, setUser]);

  const retrySession = useCallback(() => {
    void refetch();
  }, [refetch]);

  // Global unauthorized listener：唯一的"确定未登录"清态入口
  useEffect(() => {
    const handleUnauthorized = () => {
      clearAuth();
      useWatchlistStore.getState().clear();
      queryClient.clear();
    };

    window.addEventListener("auth:unauthorized", handleUnauthorized);
    return () => {
      window.removeEventListener("auth:unauthorized", handleUnauthorized);
    };
  }, [clearAuth, queryClient]);

  // ADR 0010: temp-password session -> funnel to the forced change page.
  useEffect(() => {
    const handleMustChange = () => {
      window.location.assign("/change-password");
    };
    window.addEventListener("auth:password-change-required", handleMustChange);
    return () => {
      window.removeEventListener("auth:password-change-required", handleMustChange);
    };
  }, []);

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        isAuthReady: isAuthReady && !isLoading,
        isLoading,
        sessionError: isError ? ((error as Error | null) ?? new Error("会话探测失败")) : null,
        retrySession,
        logout: handleLogout,
        refetchUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return ctx;
}
