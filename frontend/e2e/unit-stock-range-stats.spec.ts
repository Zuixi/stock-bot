/**
 * `computeStockRangeStats` 的**纯函数单测**（P1-3 brief 门禁：「useStockRangeStats 单测（250 根窗口）」）。
 *
 * 为什么放在 `e2e/` 且用 Playwright 跑：仓库没有 vitest/jest，Playwright 自带 TypeScript
 * 转译 + tsconfig `paths` 解析，`@/` 别名可直接用 —— 零新依赖（与 `unit-kline-option.spec.ts` 同款）。
 *
 * 为什么 import 纯函数层 `hooks/rangeStats` 而不是 hook 文件：hook 文件 import 了
 * `shared/api/client`，后者在模块求值期读 `import.meta.env` —— 在 Node 里会直接抛。
 * 纯函数拆出来后才"可以被单测"，这条边界是刻意留的。
 */
import { test, expect } from "@playwright/test";
import {
  RANGE_WINDOW,
  computeStockRangeStats,
} from "@/features/stock-detail/hooks/rangeStats";
import type { KLinePoint } from "@/shared/types";

/** 造一根日线：high/low 决定区间，close 决定现价。 */
function bar(high: number, low: number, close: number): KLinePoint {
  return { date: "2026-01-01", open: close, high, low, close, volume: 1, amount: 1 };
}

/** 造 n 根同样的日线。 */
function bars(n: number, high: number, low: number, close: number): KLinePoint[] {
  return Array.from({ length: n }, () => bar(high, low, close));
}

test("样本不足 250 根 → null（缺失 ≠ 0，整条不渲染）", () => {
  expect(computeStockRangeStats([])).toBeNull();
  expect(computeStockRangeStats(bars(RANGE_WINDOW - 1, 150, 100, 120))).toBeNull();
});

test("恰好 250 根即可算：high/low/距离/位置", () => {
  // 最后一根现价 120，区间 [100, 150] ⇒ 距高 -20%、距低 +20%、位置 40%
  const points = [...bars(249, 150, 100, 120), bar(150, 100, 120)];
  expect(points).toHaveLength(RANGE_WINDOW);

  const stats = computeStockRangeStats(points);
  expect(stats).not.toBeNull();
  expect(stats!.high52w).toBe(150);
  expect(stats!.low52w).toBe(100);
  expect(stats!.distanceToHigh).toBeCloseTo(-20, 6);
  expect(stats!.distanceToLow).toBeCloseTo(20, 6);
  expect(stats!.positionPct).toBeCloseTo(40, 6);
});

test("只看**最后 250 根**：更早的极值不得污染区间", () => {
  // 前 50 根是 10 年前的 1000/1 极值；后 250 根是 [100, 150]。
  // 若窗口写错（比如用了全量），high/low 会变成 1000/1。
  const points = [...bars(50, 1000, 1, 500), ...bars(250, 150, 100, 120)];
  const stats = computeStockRangeStats(points);
  expect(stats!.high52w).toBe(150);
  expect(stats!.low52w).toBe(100);
});

test("high === low（区间退化）→ null，不画一根停在 0% 的假游标", () => {
  expect(computeStockRangeStats(bars(RANGE_WINDOW, 120, 120, 120))).toBeNull();
});

test("现价越出区间时位置夹到 [0,100]（停牌/除权导致的越界不得让游标跑出轨道）", () => {
  // 收盘价高于窗口最高价（500 > 150）：位置夹到 100
  const above = [...bars(249, 150, 100, 120), bar(150, 100, 500)];
  expect(computeStockRangeStats(above)!.positionPct).toBe(100);

  // 收盘价低于窗口最低价（10 < 100）：位置夹到 0
  const below = [...bars(249, 150, 100, 120), bar(150, 100, 10)];
  expect(computeStockRangeStats(below)!.positionPct).toBe(0);
});

test("现价用最后一根收盘价（不是最大值/均值）", () => {
  const points = [...bars(249, 150, 100, 140), bar(150, 100, 110)];
  const stats = computeStockRangeStats(points)!;
  // 110 在 [100,150] 的 20% 处
  expect(stats.positionPct).toBeCloseTo(20, 6);
  expect(stats.distanceToHigh).toBeCloseTo(((110 - 150) / 150) * 100, 6);
});
