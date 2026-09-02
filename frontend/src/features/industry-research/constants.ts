/** 周期/信号展示常量 — 列表卡片与工作台共用同一套色板与文案映射。 */

/** 四周期阶段 key → 展示名（registry 各行业沿用同一键位；未知 key 回退原文） */
export const PHASE_LABELS: Record<string, string> = {
  prosperity: "繁荣",
  recession: "衰退",
  depression: "萧条",
  recovery: "复苏",
};

/** 阶段 key → 前景色（繁荣红 / 衰退金 / 萧条灰 / 复苏蓝，与原型一致） */
export const PHASE_COLORS: Record<string, string> = {
  prosperity: "#cf1322",
  recession: "#d48806",
  depression: "#4e5969",
  recovery: "#1677ff",
};

/** 信号文案 → 前景色（红涨绿跌惯例：做多=红） */
export const SIGNAL_TEXT_COLORS: Record<string, string> = {
  买入: "#cf1322",
  卖出: "#389e0d",
  关注: "#d48806",
  空仓: "#8c8c8c",
};

export function phaseLabel(key: string | null | undefined): string | null {
  if (!key) return null;
  return PHASE_LABELS[key] ?? key;
}
