import type { AdjustMode, KlineFetcher, KlineResult, KLinePoint } from "@/shared/types";

export { type AdjustMode, type KlineResult, type KlineFetcher };

export const MA_DEFS = [
  { key: "MA5", window: 5, color: "#f59e0b" },
  { key: "MA10", window: 10, color: "#3b82f6" },
  { key: "MA20", window: 20, color: "#a855f7" },
  { key: "MA60", window: 60, color: "#6b7280" },
] as const;

export type MaKey = (typeof MA_DEFS)[number]["key"];

export function movingAverage(values: number[], window: number): (number | null)[] {
  const out: (number | null)[] = [];
  let sum = 0;
  for (let i = 0; i < values.length; i++) {
    sum += values[i];
    if (i >= window) sum -= values[i - window];
    out.push(i >= window - 1 ? +(sum / window).toFixed(2) : null);
  }
  return out;
}

export type KlineFreq = "day" | "week" | "month";

/** 默认初始视图显示的末尾K线根数（TradingView 式：数据全量、视图局部、slider 漫游） */
export const DEFAULT_TAIL_BARS = 120;

/**
 * 窄屏（`<768`）默认聚焦的末尾根数（Phase 3 §4.4，B2）。
 *
 * **不是隐藏、也不是少画**：`<768` 下仍渲染全量序列与两条 `dataZoom`
 * （`inside` 支持手势缩放/拖动 + `slider` 可漫游），只是**初始视窗**从 120 根收到 30 根——
 * 308px 宽的画布里 120 根蜡烛每根 ~2.5px，红绿几乎连成色带；30 根每根 ~10px，
 * 单根阴阳线可辨。用户捏合或拖 slider 即可看任意区间（数据一根都没少）。
 */
export const NARROW_TAIL_BARS = 30;

function weekKey(date: string): string {
  const d = new Date(`${date}T00:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7)); // 回到本周周一
  return d.toISOString().slice(0, 10);
}

/** 日线 → 周/月K聚合：open=组首、close=组末、high/low=极值、volume/amount=求和 */
export function aggregateDaily(points: KLinePoint[], freq: KlineFreq): KLinePoint[] {
  if (freq === "day" || points.length === 0) return points;
  const out: KLinePoint[] = [];
  let cur: KLinePoint | null = null;
  let curKey = "";
  for (const p of points) {
    const key = freq === "week" ? weekKey(p.date) : p.date.slice(0, 7);
    if (cur && key === curKey) {
      cur.high = Math.max(cur.high, p.high);
      cur.low = Math.min(cur.low, p.low);
      cur.close = p.close;
      cur.volume += p.volume;
      cur.amount = (cur.amount ?? 0) + (p.amount ?? 0);
    } else {
      if (cur) out.push(cur);
      cur = { ...p, amount: p.amount ?? 0 };
      curKey = key;
    }
  }
  if (cur) out.push(cur);
  return out;
}

export function fmtVolume(v: number): string {
  return v >= 10000 ? `${(v / 10000).toFixed(2)}万手` : `${v}手`;
}

export function fmtAmount(a: number | undefined): string {
  return a == null ? "--" : `${(a / 100000).toFixed(2)}亿`;
}

/**
 * 涨跌幅（%）：首根 / 缺前收（含前收 0）一律 `--`，**不是** `0.00%`。
 *
 * tooltip 与图上方 ribbon 共用这一个实现 —— 两处若各写一份 `pct`，迟早在
 * 「前收为 0 该显示什么」上分叉（缺失 ≠ 0 是全局约束）。
 */
export function fmtChangePct(cur: number, prev: number | undefined): string {
  return prev == null || prev === 0 ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;
}

/** 涨跌额：带正负号；首根 / 缺前收 `--`（同上，缺失不回落 `+0.00`）。 */
export function fmtSignedDiff(cur: number, prev: number | undefined): string {
  return prev == null ? "--" : `${cur - prev >= 0 ? "+" : ""}${(cur - prev).toFixed(2)}`;
}
