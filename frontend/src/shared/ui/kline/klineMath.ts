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
