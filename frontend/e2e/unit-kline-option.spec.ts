/**
 * `buildKlineOption` 的**纯函数单测**（P0-5 的唯一回归护栏，见 brief 门禁）。
 *
 * 为什么不塞进 `kline.spec.ts`：这些断言不需要浏览器/后端，只验 option 结构；
 * 混在 e2e 里跑要等 docker 栈和 600519 的数据，回归信号会被环境噪声淹没。
 *
 * 为什么放 `e2e/` 且用 Playwright 跑：仓库没有 vitest/jest，Playwright 自带
 * TypeScript 转译 + tsconfig `paths` 解析，`@/` 别名可直接用 —— 零新依赖。
 */
import { test, expect } from "@playwright/test";
import { buildKlineOption } from "@/shared/ui/kline/klineOption";
import { MA_DEFS, movingAverage, type MaKey } from "@/shared/ui/kline/klineMath";
import type { KLinePoint } from "@/shared/types";

/**
 * 25 根合成日线（确定性、无网络）。
 *
 * 取 25 而不是 brief 说的"约 6 根"：6 根时 `MA20` 全为 `null`（窗口 20），
 * tooltip 只有 10 行，而验收口径是「完整 11 行」（8 个基础行 + MA5/10/20）。
 * 25 根让 index 24 处三条 MA 全有值 ⇒ 能逐字断言 11 行；index 3 处仍是
 * brief 指定的那个点位（此时 MA20 无值，10 行，也一并断言）。
 */
const points: KLinePoint[] = Array.from({ length: 25 }, (_, k) => {
  const base = 100 + k; // 单调上行，开=100+k、收=101+k，全部阳线
  return {
    date: `2026-03-${String(k + 1).padStart(2, "0")}`,
    open: base,
    close: base + 1,
    low: base - 2,
    high: base + 3,
    volume: 10_000 + k * 1_000,
    amount: 1_000_000 + k * 100_000,
  };
});

const maSeries = MA_DEFS.reduce<Partial<Record<MaKey, (number | null)[]>>>((acc, def) => {
  acc[def.key] = movingAverage(
    points.map((p) => p.close),
    def.window,
  );
  return acc;
}, {});

const option = buildKlineOption({
  points,
  maSeries,
  visibleMas: ["MA5", "MA10", "MA20"],
});

/** tooltip 每一行都是 `<div style="display:flex;justify-content:space-between...">` */
const rowCount = (html: string) => html.split("justify-content:space-between").length - 1;

test("buildKlineOption：axisPointer.link 把两个 x 轴绑成一条十字光标", () => {
  // 竖线要贯穿 K 线 pane 与成交量 pane，靠的就是这行顶层 link（缺它 = 用户报的 F4）
  expect(option.axisPointer.link).toEqual([{ xAxisIndex: "all" }]);
  expect(option.axisPointer.lineStyle).toMatchObject({ type: "dashed", width: 1 });
  expect(option.axisPointer.label.fontSize).toBe(10);
});

test("buildKlineOption：两个 xAxis 都声明 snap + mousemove|click 的 axisPointer", () => {
  expect(option.xAxis).toHaveLength(2);
  for (const [i, axis] of option.xAxis.entries()) {
    // ⚠️ 轴级 `type` 只能是 line/shadow。写 "cross" 会被模型链（轴级优先级最高，
    // 见 echarts modelHelper.makeAxisPointerModel）盖掉 tooltip 派生出的 "line"，
    // 随后 CartesianAxisPointer 执行 pointerShapeBuilder["cross"](...) ⇒
    // `pointerShapeBuilder[axisPointerType] is not a function` PAGEERROR，
    // 整张图的十字光标和 tooltip 一起消失（实测 echarts 5.6.0）。
    expect(axis.axisPointer.type, `xAxis[${i}].axisPointer.type`).not.toBe("cross");
    expect(axis.axisPointer.type, `xAxis[${i}].axisPointer.type`).toBe("line");
    // snap 让竖线吸附蜡烛中心（而不是落在两根之间）
    expect(axis.axisPointer.snap, `xAxis[${i}].axisPointer.snap`).toBe(true);
    // 带 click 才有触屏十字
    expect(axis.axisPointer.triggerOn, `xAxis[${i}].axisPointer.triggerOn`).toBe("mousemove|click");
  }
});

test("buildKlineOption：cross 语义只由 tooltip + 顶层 axisPointer 提供", () => {
  // 「竖线贯穿两个 pane」= 顶层 link；「横线只在本 pane + 双轴标签」= tooltip 的 cross。
  // 这两者合起来才是 brief 的"cross"，而不是把 type 塞进 xAxis。
  expect(option.tooltip.axisPointer.type).toBe("cross");
  expect(option.axisPointer.link).toEqual([{ xAxisIndex: "all" }]);
  expect(option.axisPointer.triggerOn).toBe("mousemove|click");
});

test("buildKlineOption：formatter 靠任意 series 定位 —— 成交量 pane 的 bar 也能出完整 tooltip", () => {
  // 悬停成交量 pane 时 ECharts 只把 bar 塞进 params（candlestick 在另一条 axis 上），
  // 旧实现 find(seriesType==="candlestick") 返回 undefined ⇒ 空 tooltip（F5）
  const fromBar = option.tooltip.formatter([{ dataIndex: 3, seriesType: "bar" }]);
  expect(fromBar.length).toBeGreaterThan(0);
  expect(fromBar).toContain(points[3].date);
  // 11 行口径在 index 3 上只能拿到 10 行（MA20 未成形），故用后段点位验"完整 11 行"
  const fromBarTail = option.tooltip.formatter([{ dataIndex: 24, seriesType: "bar" }]);
  expect(rowCount(fromBarTail)).toBe(11); // 开/收/高/低/涨跌额/涨跌幅/成交量/成交额 + MA5/10/20
  for (const label of ["开盘", "收盘", "最高", "最低", "涨跌额", "涨跌幅", "成交量", "成交额"]) {
    expect(fromBarTail, label).toContain(label);
  }
  expect(fromBarTail).toContain(points[24].date);

  // 从 candlestick 侧进来必须是**同一段** HTML：两条路径共用 points 回查，不该有分叉
  const fromCandle = option.tooltip.formatter([{ dataIndex: 3, seriesType: "candlestick" }]);
  expect(fromCandle).toBe(fromBar);
  expect(fromCandle).toContain(points[3].date);
  for (const label of ["开盘", "收盘", "成交量", "成交额"]) {
    expect(fromCandle, label).toContain(label);
  }
});

test("buildKlineOption：formatter 对空/越界 params 返回空串（不抛异常）", () => {
  expect(option.tooltip.formatter([])).toBe("");
  expect(option.tooltip.formatter([{ dataIndex: 999, seriesType: "bar" }])).toBe("");
});

test("buildKlineOption：tooltip.confine 只在窄屏开启", () => {
  const wide = option.tooltip as { confine?: boolean };
  expect(wide.confine).toBeFalsy();
  const narrow = buildKlineOption({ points, maSeries, visibleMas: ["MA5"], narrow: true }).tooltip as {
    confine?: boolean;
  };
  expect(narrow.confine).toBe(true);
});
