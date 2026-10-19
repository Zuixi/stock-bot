import type { KLinePoint } from "@/shared/types";

/**
 * 52 周区间统计的**纯函数层**（不 import 任何 API/react-query，便于单测）。
 *
 * 查询与 hooks 编排在 `useStockRangeStats.ts`；这里只做"给定日线序列 → 区间数字"。
 * 分成两个文件的原因很具体：`shared/api/client.ts` 在模块求值期读 `import.meta.env`，
 * 纯函数单测（`e2e/unit-stock-range-stats.spec.ts`，Playwright 直跑 TS）一旦顺手 import
 * 带网络层的模块就会在 Node 里炸掉，于是"能单测"退化成"只能 e2e"。
 */

/** 52 周窗口 = 250 个交易日（A 股一年约 242–245 根，250 给停牌留余量）。 */
export const RANGE_WINDOW = 250;

/** K 线窗口（与 `KlineChart` 的 `fetcher(3650, adjust)` 逐字一致，否则缓存键相同也取不到同一份数据）。 */
export const RANGE_FETCH_DAYS = 3650;

export interface StockRangeStats {
  /** 最近 250 根的最高价（qfq 口径，与 K 线图同源） */
  high52w: number;
  /** 最近 250 根的最低价 */
  low52w: number;
  /** 距 52 周高点（%）：现价在上方时为负，如 -18.3 */
  distanceToHigh: number;
  /** 距 52 周低点（%）：现价在下方为正，如 +12.4 */
  distanceToLow: number;
  /** 现价在区间中的位置（0–100），直接用于区间条游标 */
  positionPct: number;
}

/**
 * 从日线序列算 52 周区间统计。
 *
 * **样本不足即 `null`**（缺失 ≠ 0）：上市不满 250 根时返回 `null`，调用方整条不渲染；
 * `high52w === low52w`（长期停牌/一根 K 线）时分母为 0，同样返回 `null` —— 宁可没有区间条，
 * 也不要画一根游标停在 0% 的假区间。
 */
export function computeStockRangeStats(points: KLinePoint[]): StockRangeStats | null {
  if (points.length < RANGE_WINDOW) return null;

  const window = points.slice(-RANGE_WINDOW);
  let high52w = Number.NEGATIVE_INFINITY;
  let low52w = Number.POSITIVE_INFINITY;
  for (const point of window) {
    if (Number.isFinite(point.high)) high52w = Math.max(high52w, point.high);
    if (Number.isFinite(point.low)) low52w = Math.min(low52w, point.low);
  }

  // 现价取**同一序列**的最后一根收盘价：区间与游标必须同口径（都来自 qfq 日线），
  // 混用名录里的 `latestPrice`（raw 口径）会让游标与轨道不是同一条曲线上的点。
  const price = points[points.length - 1]?.close;

  if (!Number.isFinite(high52w) || !Number.isFinite(low52w) || !Number.isFinite(price)) return null;
  if (high52w <= low52w) return null;

  return {
    high52w,
    low52w,
    distanceToHigh: ((price - high52w) / high52w) * 100,
    distanceToLow: ((price - low52w) / low52w) * 100,
    positionPct: Math.min(100, Math.max(0, ((price - low52w) / (high52w - low52w)) * 100)),
  };
}
