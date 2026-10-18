/**
 * `alignMetricSeries` 的**纯函数单测**（P1-4 / F12 的回归护栏）。
 *
 * 被测缺陷（旧 `MetricsHistoryChart`）：x 轴取 `histories[0].points`，各序列按**位置**贴值。
 * 只要有一个指标缺一期，后面所有值就整体左移一格 —— 图看着平滑，读数全错，而且
 * 编译、类型、渲染全都不报错。所以把"期间并集 + 按 period 贴值 + 缺期 = null"钉成测试。
 *
 * 为什么放 `e2e/` 且用 Playwright 跑：仓库没有 vitest/jest，Playwright 自带 TypeScript
 * 转译 + tsconfig `paths` 解析，`@/` 别名可直接用（与 `unit-kline-option.spec.ts` 同款）。
 */
import { expect, test } from "@playwright/test";

import { alignMetricSeries } from "@/features/stock-detail/lib/metricSeries";
import type { MetricHistory } from "@/shared/api/financial";

/** 造一条指标历史：`[period, value]` 列表 → MetricHistory（unit 与本测无关） */
function history(
  metricKey: string,
  points: Array<[string, number | null]>,
): MetricHistory {
  return {
    metric_key: metricKey,
    unit: "%",
    points: points.map(([period, value]) => ({ period, value, unit: "%" })),
  };
}

test.describe("alignMetricSeries：期间并集 + 按 period 对齐（缺失 ≠ 0）", () => {
  test("① 各指标期间完全一致：并集就是这些期间，逐点取值不动", () => {
    const { periods, series } = alignMetricSeries([
      history("roe", [
        ["2025-03-31", 30.1],
        ["2025-06-30", 31.2],
      ]),
      history("gross_margin", [
        ["2025-03-31", 91.5],
        ["2025-06-30", 92.0],
      ]),
    ]);

    expect(periods).toEqual(["2025-03-31", "2025-06-30"]);
    expect(series).toEqual([
      { name: "roe", values: [30.1, 31.2] },
      { name: "gross_margin", values: [91.5, 92.0] },
    ]);
  });

  test("② 某指标缺**中间**一期：该序列该位置是 null，期间仍包含它（不左移）", () => {
    const { periods, series } = alignMetricSeries([
      history("roe", [
        ["2025-03-31", 30.1],
        ["2025-06-30", 31.2],
        ["2025-09-30", 32.3],
      ]),
      // gross_margin 缺中间一期（2025-06-30 未披露）
      history("gross_margin", [
        ["2025-03-31", 91.5],
        ["2025-09-30", 92.5],
      ]),
    ]);

    expect(periods).toEqual(["2025-03-31", "2025-06-30", "2025-09-30"]);
    // 关键断言：缺的那期是 null，而不是把 92.5 顶到 index 1（旧实现就是这样错位的）
    expect(series[1]).toEqual({ name: "gross_margin", values: [91.5, null, 92.5] });
    expect(series[0]).toEqual({ name: "roe", values: [30.1, 31.2, 32.3] });
  });

  test("③ 某指标缺**首期与末期**：首尾都是 null，中间值不位移", () => {
    const { periods, series } = alignMetricSeries([
      history("roe", [
        ["2024-12-31", 28.0],
        ["2025-03-31", 30.1],
        ["2025-06-30", 31.2],
        ["2025-09-30", 32.3],
      ]),
      // 只披露了中间两期
      history("revenue_yoy", [
        ["2025-03-31", -1.5],
        ["2025-06-30", 2.4],
      ]),
    ]);

    expect(periods).toEqual([
      "2024-12-31",
      "2025-03-31",
      "2025-06-30",
      "2025-09-30",
    ]);
    expect(series[1]).toEqual({
      name: "revenue_yoy",
      values: [null, -1.5, 2.4, null],
    });
  });

  test("④ 空输入：periods 与 series 都为空（不炸、不造一根空轴）", () => {
    expect(alignMetricSeries([])).toEqual({ periods: [], series: [] });
  });

  test("⑤ 所有指标 `points: []`：periods 为空，序列保留但值数组为空", () => {
    const { periods, series } = alignMetricSeries([
      { metric_key: "roe", unit: "%", points: [] },
      { metric_key: "gross_margin", unit: "%", points: [] },
      { metric_key: "net_margin", unit: "%", points: [] },
    ]);

    expect(periods).toEqual([]);
    // 序列身份不丢（调用方按 histories 顺序映射中文名），但没有任何期间
    expect(series).toEqual([
      { name: "roe", values: [] },
      { name: "gross_margin", values: [] },
      { name: "net_margin", values: [] },
    ]);
  });

  test("⑥ 不论入参顺序如何，periods 恒为升序", () => {
    const shuffled = [
      history("roe", [
        ["2025-09-30", 32.3],
        ["2025-03-31", 30.1],
      ]),
      history("gross_margin", [
        ["2025-06-30", 92.0],
        ["2024-12-31", 90.0],
      ]),
    ];
    const { periods, series } = alignMetricSeries(shuffled);

    expect(periods).toEqual([
      "2024-12-31",
      "2025-03-31",
      "2025-06-30",
      "2025-09-30",
    ]);
    // 值跟着自己的 period 走：roe 的 30.1 落在 2025-03-31（index 1），32.3 落在 index 3
    expect(series[0]).toEqual({ name: "roe", values: [null, 30.1, null, 32.3] });
    expect(series[1]).toEqual({ name: "gross_margin", values: [90.0, null, 92.0, null] });

    // 入参顺序（先 09 后 03）不影响结果
    const reversed = alignMetricSeries([...shuffled].reverse());
    expect(reversed.periods).toEqual(periods);
  });
});
