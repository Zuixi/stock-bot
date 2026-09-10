import { theme } from "antd";
import type { ThemeConfig } from "antd";

export type ThemeMode = "light" | "dark";

/** 明暗双主题调色板（与 styles/theme.css 的 CSS 变量一一对应，取值见契约 §1） */
export interface ThemePalette {
  /** --bg-page 页面底色 */
  bgPage: string;
  /** --bg-panel 面板/卡片底色 */
  bgPanel: string;
  /** --bg-elevated 浮层底色 */
  bgElevated: string;
  /** --border 边框/分隔线 */
  border: string;
  /** --text-primary 主文本 */
  textPrimary: string;
  /** --text-secondary 次级文本（兼作 flat 中性色） */
  textSecondary: string;
  /** --accent 品牌强调色 */
  accent: string;
  /** A股红涨 */
  up: string;
  /** A股绿跌 */
  down: string;
  /** 中性/占位色（对齐 --text-secondary） */
  flat: string;
  /** 图表主色（= accent） */
  primary: string;
  /** --hover 悬浮背景 */
  hover: string;
}

/**
 * TradingView 风格明暗双主题色板。
 * 契约：docs/design/landing-market-theme.md §1。
 * ECharts 不消费 CSS var，canvas 图表一律从这里取当前模式的具体色值。
 */
export const THEME_COLORS: Record<ThemeMode, ThemePalette> = {
  light: {
    bgPage: "#ffffff",
    bgPanel: "#f8f9fd",
    bgElevated: "#ffffff",
    border: "#e0e3eb",
    textPrimary: "#131722",
    textSecondary: "#6a6d78",
    accent: "#2962ff",
    up: "#c62828",
    down: "#0a7d5f",
    flat: "#6a6d78",
    primary: "#2962ff",
    hover: "rgba(41, 98, 255, 0.06)",
  },
  dark: {
    bgPage: "#131722",
    bgPanel: "#1e222d",
    bgElevated: "#232837",
    border: "#2a2e39",
    textPrimary: "#d1d4dc",
    textSecondary: "#787b86",
    accent: "#2962ff",
    up: "#f23645",
    down: "#089981",
    flat: "#787b86",
    primary: "#2962ff",
    hover: "rgba(41, 98, 255, 0.12)",
  },
};

/** AntD 主题工厂：light 走 defaultAlgorithm，dark 走 darkAlgorithm，token 映射契约 §1 */
export function buildAntdTheme(mode: ThemeMode): ThemeConfig {
  const t = THEME_COLORS[mode];
  return {
    algorithm: mode === "dark" ? theme.darkAlgorithm : theme.defaultAlgorithm,
    token: {
      colorPrimary: t.accent,
      borderRadius: 6,
      fontSize: 14,
      colorBgBase: t.bgPage,
      colorBgContainer: t.bgPanel,
      colorBgElevated: t.bgElevated,
      colorBorder: t.border,
      colorText: t.textPrimary,
      colorTextSecondary: t.textSecondary,
    },
    components: {
      Layout: {
        headerBg: t.bgPanel,
        siderBg: t.bgPanel,
      },
    },
  };
}

/**
 * @deprecated 旧静态色板（仅浅色）。新代码请用 `useTheme().colors` 或 `THEME_COLORS[mode]`，
 * 暗色下静态取值不会跟随切换。存量引用将在 Stage C 逐步迁移。
 */
export const COLORS = THEME_COLORS.light;
