/**
 * 市场状态机的五态**展示元数据**（S10 §S10.3，2026-10-19 owner 批准"颜色区分"）。
 *
 * 三条设计约束（写在类型旁边，改之前先读）：
 *
 * 1. **状态 ≠ 涨跌**：五态走**冷暖光谱**（蓝→青→灰→橙→紫），**刻意避开** `--up`（涨红）
 *    与 `--down`（跌绿）——那两色只属于数字，给状态上红绿会让"亢奋/退潮"被读成"涨/跌"。
 * 2. **不只靠颜色**：每处色块旁必须有状态名；对比度由 `theme.css` 的令牌保证
 *    （对白底 AA ≥4.5，深色主题 ≥7.8，实测值见 `docs/specs/S10-market-regime.md`）。
 * 3. **五态次序固定** = `REGIME_STATE_ORDER`（判定优先级），展示层不得按概率重排。
 */

import { REGIME_STATE_ORDER } from "@/shared/api/regime";

/** 五态的中文名（与后端 `state` 字符串逐字一致）。 */
export type RegimeStateName = "冰点" | "修复" | "中性" | "亢奋" | "退潮";

export interface RegimeStateMeta {
  /** CSS 令牌名（`--regime-*`），组件里通过 className 引用，不写死色值。 */
  readonly token: string;
  /** 一个字的形状提示（色盲用户与打印场景的第二通道；不上屏成图标）。 */
  readonly shape: string;
}

export const REGIME_STATE_META: Record<RegimeStateName, RegimeStateMeta> = {
  冰点: { token: "var(--regime-cold)", shape: "◆" },
  修复: { token: "var(--regime-warming)", shape: "▲" },
  中性: { token: "var(--regime-neutral)", shape: "■" },
  亢奋: { token: "var(--regime-hot)", shape: "▲" },
  退潮: { token: "var(--regime-cooling)", shape: "▼" },
};

/** 状态名 → 元数据；未知状态返回 `null`（调用方保持中性样式，不猜颜色）。 */
export function regimeMeta(state: string): RegimeStateMeta | null {
  return REGIME_STATE_META[state as RegimeStateName] ?? null;
}

/** 状态名 → 稳定的 class 后缀（`regime--cold` 等），样式在 CSS 里按状态写。 */
export function regimeClass(state: string): string {
  switch (state) {
    case "冰点":
      return "regime--cold";
    case "修复":
      return "regime--warming";
    case "中性":
      return "regime--neutral";
    case "亢奋":
      return "regime--hot";
    case "退潮":
      return "regime--cooling";
    default:
      return "regime--unknown";
  }
}

/** 五态的固定展示次序（= 判定优先级，见 S10 §S10.2）。 */
export const REGIME_DISPLAY_ORDER: readonly string[] = REGIME_STATE_ORDER;
