import type { AdjustMode, KlineResult, KlineFetcher } from "@/shared/types";

export { type AdjustMode, type KlineResult, type KlineFetcher };

export const MA_DEFS = [
  { key: "MA5", window: 5, color: "#f59e0b" },
  { key: "MA10", window: 10, color: "#3b82f6" },
  { key: "MA20", window: 20, color: "#a855f7" },
  { key: "MA60", window: 60, color: "#6b7280" },
] as const;

export type MaKey = (typeof MA_DEFS)[number]["key"];

/** 60 个交易日 ≈ 91 个日历日，取 130 留节假日缓冲 */
export const MA_WARMUP_CALENDAR_DAYS = 130;

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

export function buildAxisLabels(dates: string[]): string[] {
  return dates.map((d, i) => (i > 0 && d.slice(0, 4) !== dates[i - 1].slice(0, 4) ? d : d.slice(5)));
}

export function cropToRange<T extends { date: string }>(items: T[], cutoffDate: string): T[] {
  const start = items.findIndex((p) => p.date >= cutoffDate);
  return start <= 0 ? items : items.slice(start);
}

export function fmtVolume(v: number): string {
  return v >= 10000 ? `${(v / 10000).toFixed(2)}万手` : `${v}手`;
}

export function fmtAmount(a: number | undefined): string {
  return a == null ? "--" : `${(a / 100000).toFixed(2)}亿`;
}
