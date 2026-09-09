import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { THEME_COLORS, type ThemeMode, type ThemePalette } from "@/app/theme";

const STORAGE_KEY = "stockbot-theme";

interface ThemeContextValue {
  mode: ThemeMode;
  toggle: () => void;
  /** 当前模式的具体色值（ECharts 等 canvas 场景不吃 CSS var，从这里取 hex） */
  colors: ThemePalette;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

/** 暗色判定优先级：localStorage('stockbot-theme') > prefers-color-scheme（契约 §1） */
function resolveInitialMode(): ThemeMode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // localStorage 不可用（隐私模式等）时回退系统偏好
  }
  if (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches) {
    return "dark";
  }
  return "light";
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ThemeMode>(resolveInitialMode);

  useEffect(() => {
    document.documentElement.dataset.theme = mode;
    try {
      localStorage.setItem(STORAGE_KEY, mode);
    } catch {
      // 持久化失败不阻断当次切换
    }
  }, [mode]);

  const value = useMemo<ThemeContextValue>(
    () => ({
      mode,
      toggle: () => setMode((m) => (m === "dark" ? "light" : "dark")),
      colors: THEME_COLORS[mode],
    }),
    [mode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme 必须在 ThemeProvider 内使用");
  return ctx;
}
