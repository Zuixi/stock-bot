import type { ThemeConfig } from "antd";

export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: "#1677ff",
    borderRadius: 6,
    colorBgContainer: "#ffffff",
    fontSize: 14,
  },
  components: {
    Layout: {
      headerBg: "#ffffff",
      siderBg: "#ffffff",
    },
  },
};

export const COLORS = {
  up: "#ef4444",
  down: "#22c55e",
  flat: "#9ca3af",
  primary: "#1677ff",
  bg: "#f5f5f5",
} as const;
