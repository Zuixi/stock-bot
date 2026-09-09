import React, { createContext, useContext, useEffect, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getSession, logout as logoutApi, type UserProfile } from "@/shared/api/auth";
import { useWatchlistStore } from "@/features/watchlist/store";
import { useAuthStore } from "./store";

interface AuthContextValue {
  user: UserProfile | null;
  isAuthenticated: boolean;
  isAuthReady: boolean;
  isLoading: boolean;
  logout: () => Promise<void>;
  refetchUser: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const queryClient = useQueryClient();
  const { user, isAuthenticated, isAuthReady, setUser, clearAuth, setAuthReady } = useAuthStore();

  const { data, isLoading, refetch } = useQuery<UserProfile | null>({
    queryKey: ["auth", "session"],
    queryFn: async () => {
      try {
        return await getSession({ skipAuth: true });
      } catch {
        return null;
      }
    },
    staleTime: 5 * 60 * 1000,
    retry: false,
    refetchOnWindowFocus: true,
  });

  useEffect(() => {
    if (!isLoading) {
      setUser(data ?? null);
    }
  }, [data, isLoading, setUser]);

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
    setUser(res.data ?? null);
  }, [refetch, setUser]);

  // Global unauthorized listener
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

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated,
        isAuthReady: isAuthReady && !isLoading,
        isLoading,
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
