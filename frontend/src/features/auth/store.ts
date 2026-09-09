import { create } from "zustand";
import type { UserProfile } from "@/shared/api/auth";

export interface AuthState {
  user: UserProfile | null;
  isAuthenticated: boolean;
  isAuthReady: boolean;
  setUser: (user: UserProfile | null) => void;
  setAuthReady: (ready: boolean) => void;
  hasRole: (role: string | string[]) => boolean;
  hasPermission: (perm: string | string[]) => boolean;
  clearAuth: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  user: null,
  isAuthenticated: false,
  isAuthReady: false,

  setUser: (user) => {
    set({
      user,
      isAuthenticated: !!user,
      isAuthReady: true,
    });
  },

  setAuthReady: (ready) => {
    set({ isAuthReady: ready });
  },

  hasRole: (role) => {
    const { user } = get();
    if (!user) return false;
    if (user.is_superuser) return true;
    const requiredRoles = Array.isArray(role) ? role : [role];
    return requiredRoles.some((r) => user.roles?.includes(r));
  },

  hasPermission: (perm) => {
    const { user } = get();
    if (!user) return false;
    if (user.is_superuser) return true;
    const requiredPerms = Array.isArray(perm) ? perm : [perm];
    return requiredPerms.some((p) => user.permissions?.includes(p));
  },

  clearAuth: () => {
    set({
      user: null,
      isAuthenticated: false,
      isAuthReady: true,
    });
  },
}));
