/**
 * ribbon（图上方数据条）与 MA 取值的**纯函数单测**（P1-1 / F10 的回归护栏）。
 *
 * 放在 `e2e/` 且用 Playwright 跑的理由与 `unit-kline-option.spec.ts` 相同：仓库没有
 * vitest/jest，Playwright 自带 TS 转译 + tsconfig `paths`，`@/` 别名可直接用 —— 零新依赖。
 * 这些断言不需要浏览器/后端：只验"给定序列与索引，这一行该显示什么"。
 */
import { test, expect } from "@playwright/test";
import { maValuesAt, ribbonRow } from "@/shared/ui/kline/ribbon";
import { fmtAmount, fmtVolume, type MaKey } from "@/shared/ui/kline/klineMath";
import type { KLinePoint } from "@/shared/types";

/** 3 根确定性日线：第 0 根没有前收；第 1 根 volume < 1 万手；第 2 根金额有值。 */
const points: KLinePoint[] = [
  { date: "2026-03-02", open: 10, close: 10.5, high: 10.8, low: 9.9, volume: 12_345, amount: 1_234_500 },
  { date: "2026-03-03", open: 10.5, close: 10.9, high: 11, low: 10.4, volume: 9_999 },
  { date: "2026-03-04", open: 10.9, close: 10.2, high: 11.1, low: 10.1, volume: 20_000, amount: 2_000_000 },
];

test("ribbonRow：普通索引取到该根的完整一行（价格两位小数）", () => {
  const row = ribbonRow(points, 2);
  expect(row).not.toBeNull();
  expect(row).toEqual({
    date: "2026-03-04",
    open: "10.90",
    high: "11.10",
    low: "10.10",
    close: "10.20",
    // (10.2 - 10.9) / 10.9 = -6.422…% —— 与 tooltip 同一个 fmtChangePct
    changePct: "-6.42%",
    volume: "2.00万手",
    amount: "20.00亿",
  });
});

test("ribbonRow：首根没有前收 ⇒ changePct 是 `--`，不是 `0.00%`（tooltip 同一规则）", () => {
  const row = ribbonRow(points, 0);
  expect(row).not.toBeNull();
  // 首根 close=10.5、open=10 —— 若按"收盘 vs 开盘"算会得到 +5.00%，那是另一个口径
  expect(row!.changePct).toBe("--");
  // 其余字段照常可读（缺失语义只作用于"需要前收"的那一格）
  expect(row!.date).toBe("2026-03-02");
  expect(row!.close).toBe("10.50");
});

test("ribbonRow：索引越界/非整数 ⇒ null（调用方回落最后一根）", () => {
  expect(ribbonRow(points, -1)).toBeNull();
  expect(ribbonRow(points, points.length)).toBeNull();
  expect(ribbonRow(points, 999)).toBeNull();
  // 事件里的 value 万一是浮点/NaN，也不许"读到一个不存在的 K 线"
  expect(ribbonRow(points, 1.5)).toBeNull();
  expect(ribbonRow(points, Number.NaN)).toBeNull();
});

test("ribbonRow：空序列 ⇒ null（不抛异常）", () => {
  expect(ribbonRow([], 0)).toBeNull();
  expect(ribbonRow([], -1)).toBeNull();
});

test("maValuesAt：null MA 渲染 `--`，缺失 ≠ 0；越界同样 `--`", () => {
  const maSeries: Partial<Record<MaKey, (number | null)[]>> = {
    MA5: [10.1, null, 10.3],
    MA20: [null, null, null],
    MA60: [9.9, 9.95, 10.0],
  };
  // 第 1 根：MA5 为 null（这组数据里窗口未成形）、MA20 全 null
  expect(maValuesAt(maSeries, ["MA5", "MA20"], 1)).toEqual({ MA5: "--", MA20: "--" });
  // 有值的一格保留两位小数；全 null 的仍是 `--`
  expect(maValuesAt(maSeries, ["MA5", "MA20"], 2)).toEqual({ MA5: "10.30", MA20: "--" });
  // 索引越界：一律 `--`，绝不回落到最后一根或 0
  expect(maValuesAt(maSeries, ["MA5", "MA60"], 99)).toEqual({ MA5: "--", MA60: "--" });
  // 负数/非整数索引同样无效
  expect(maValuesAt(maSeries, ["MA5"], -1)).toEqual({ MA5: "--" });
  expect(maValuesAt(maSeries, ["MA5"], 0.5)).toEqual({ MA5: "--" });
  // 序列整个缺失也是 `--`
  expect(maValuesAt({}, ["MA5"], 0)).toEqual({ MA5: "--" });
  // 只返回可见的键：隐藏的 MA 不产生格子（由 DOM 行决定显示名字还是数值）
  expect(Object.keys(maValuesAt(maSeries, ["MA5"], 0))).toEqual(["MA5"]);
});

test("ribbonRow：数值格式与 fmtVolume/fmtAmount 逐字一致（同一根 K 线不许两种写法）", () => {
  for (const [i, p] of points.entries()) {
    const row = ribbonRow(points, i)!;
    expect(row.volume, `volume@${i}`).toBe(fmtVolume(p.volume));
    expect(row.amount, `amount@${i}`).toBe(fmtAmount(p.amount));
    expect(row.open, `open@${i}`).toBe(p.open.toFixed(2));
    expect(row.high, `high@${i}`).toBe(p.high.toFixed(2));
    expect(row.low, `low@${i}`).toBe(p.low.toFixed(2));
    expect(row.close, `close@${i}`).toBe(p.close.toFixed(2));
  }
  // 缺失的 amount 是 `--`（points[1] 没有 amount）——不是 "0.00亿"
  expect(ribbonRow(points, 1)!.amount).toBe("--");
  // 万手阈值（10000）两侧分别是"手"与"万手"，与 tooltip 共用 fmtVolume 的边界
  expect(ribbonRow(points, 1)!.volume).toBe("9999手");
  expect(ribbonRow(points, 2)!.volume).toBe("2.00万手");
});
