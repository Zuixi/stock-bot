import { useAuthStore } from "@/features/auth/store";

export interface LandingCta {
  /** 已登录「进入工作台」→ /market；未登录「免费开始」→ /login（契约 §0） */
  to: string;
  label: string;
  /** 会话探测未完成时为 false——CTA 隐藏文字防错标签闪现，布局位保留 */
  ready: boolean;
}

/** 宣传页 CTA 登录态感知（只读 auth store，不改 auth 模块） */
export function useLandingCta(): LandingCta {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const isAuthReady = useAuthStore((s) => s.isAuthReady);

  if (isAuthenticated) {
    return { to: "/market", label: "进入工作台", ready: isAuthReady };
  }
  return { to: "/login", label: "免费开始", ready: isAuthReady };
}
