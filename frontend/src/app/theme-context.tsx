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

/**
 * 暗色判定优先级：index.html 首帧引导已落的 data-theme > localStorage('stockbot-theme')
 * > prefers-color-scheme（契约 §1）。
 *
 * 首位是 index.html 里的内联引导脚本（防 FOUC，见该处注释）：挂载时若属性已存在就沿用它的
 * 结论，避免 Provider 再判一次把主题翻回去（翻动 = 多一次白→暗过渡）。后两条是引导未执行
 * （脚本被拦/内联被 CSP 挡住）时的兜底，不是死代码。
 */
function resolveInitialMode(): ThemeMode {
  const bootstrapped = document.documentElement.dataset.theme;
  if (bootstrapped === "light" || bootstrapped === "dark") return bootstrapped;
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
