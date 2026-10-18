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
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { buildKlineOption } from "@/shared/ui/kline/klineOption";
import { MA_DEFS, fmtVolume, movingAverage, type MaKey } from "@/shared/ui/kline/klineMath";
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

/** `yAxis[i]` 在返回值里是异构对象数组，访问可选字段前先收窄（避免每处断言都写类型体操）。 */
interface AxisLike {
  gridIndex?: number;
  splitNumber?: number;
  axisLabel?: { show?: boolean; formatter?: (v: number) => string };
}

test("buildKlineOption：成交量轴有可读刻度（fmtVolume 口径，splitNumber 仍为 2）", () => {
  // F9：`axisLabel.show:false` 时量能只能靠 hover 读，图上完全没有"多大"的参照
  const volumeAxis = option.yAxis[1] as unknown as AxisLike;
  expect(volumeAxis.axisLabel?.show).toBe(true);
  // 刻度数量不变：成交量 pane 只有 16% 高，再多的刻度会挤成一串
  expect(volumeAxis.splitNumber).toBe(2);
  // 单位与 tooltip 同源（手 / 万手），不是自己另起一套"亿股"
  expect(volumeAxis.axisLabel?.formatter?.(12_345)).toBe(fmtVolume(12_345));
  expect(volumeAxis.axisLabel?.formatter?.(9_999)).toBe("9999手");

  // 窄屏同样开刻度，且版式（左轴 46px）不变
  const narrowOption = buildKlineOption({ points, maSeries, visibleMas: ["MA5"], narrow: true });
  const narrowAxis = narrowOption.yAxis[1] as unknown as AxisLike;
  expect(narrowAxis.axisLabel?.show).toBe(true);
  expect(narrowAxis.axisLabel?.formatter?.(12_345)).toBe(fmtVolume(12_345));
  expect((narrowOption.grid as Array<{ left: number }>)[1].left).toBe(46);

  // 价格 pane 与成交量 pane 的 y 轴标签列**纵向不相交**：grid[0] 从 28px 起、高 50%；
  // grid[1] 从 68% 才开始 ⇒ 两列同处左侧也不会互相压住（400px 画布实测见 P1-1 报告）。
  const grids = option.grid as Array<{ top?: number | string; height?: number | string }>;
  expect(grids[0].height).toBe("50%");
  expect(grids[1].top).toBe("68%");
});

test("KlineChart：hover 索引只驱动 DOM，不进 buildKlineOption 的 useMemo 依赖", () => {
  // 机械门禁（brief 要求）：option 是 2400+ 点的大对象，hover 若成为 useMemo 依赖，
  // 鼠标每动一像素都要重建 + `notMerge` 重画 ⇒ 图明显掉帧（且 hover 状态在
  // `e2e/kline.spec.ts` 里只表现为 ribbon 日期变化，掉帧本身没人拦得住）。
  // 这里直接读源码断言依赖数组，而不是渲染 React 组件：仓库没有 RTL/vitest，
  // 而 option 的引用稳定恰好是"依赖数组里有什么"这一条静态事实。
  const source = readFileSync(join(process.cwd(), "src/shared/ui/kline/KlineChart.tsx"), "utf8");
  const callAt = source.indexOf("buildKlineOption({");
  expect(callAt, "KlineChart 必须调用 buildKlineOption").toBeGreaterThan(-1);
  const memoAt = source.lastIndexOf("useMemo(", callAt);
  expect(memoAt, "buildKlineOption 必须包在 useMemo 里").toBeGreaterThan(-1);
  const memoEnd = source.indexOf(");", callAt);
  const memoBlock = source.slice(memoAt, memoEnd);
  // 参数与依赖数组里都不许出现 hover（hoveredIndex 只喂 ribbon / MA 行）
  expect(memoBlock).not.toMatch(/hover/i);
  const depsStart = memoBlock.lastIndexOf("[");
  const depsEnd = memoBlock.indexOf("]", depsStart);
  expect(depsStart, "找不到 useMemo 的依赖数组").toBeGreaterThan(-1);
  expect(
    memoBlock
      .slice(depsStart + 1, depsEnd)
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean),
  ).toEqual(["points", "maSeries", "visibleMas", "colors", "narrow", "eventMarkers"]);
});

/** `series[0]` 是异构数组里唯一带 markPoint 的一条，读它之前先收窄。 */
interface MarkPointLike {
  symbolSize?: number;
  data?: Array<{
    name?: string;
    coord?: [string, number];
    value?: string;
    itemStyle?: { color?: string };
    label?: { show?: boolean };
  }>;
}

const candlestickMarkPoint = (input: Parameters<typeof buildKlineOption>[0]) => {
  const built = buildKlineOption(input);
  return (built.series[0] as unknown as { markPoint?: MarkPointLike }).markPoint;
};

test("buildKlineOption：事件标记只在日期命中 bar 时生成 markPoint（气泡小而无声）", () => {
  // 默认（无事件）：结构仍在、数据为空 —— 调用方不必区分 "undefined" 与 "空数组"
  const empty = candlestickMarkPoint({ points, maSeries, visibleMas: [] });
  expect(empty?.data).toEqual([]);

  const marker = { date: points[5].date, label: "公告+1", color: "#123456" };
  const outOfRange = { date: "1990-01-01", label: "解禁", color: "#654321" };
  const markPoint = candlestickMarkPoint({
    points, maSeries, visibleMas: [], eventMarkers: [marker, outOfRange],
  });

  // 区间外（上市前/停牌）不产生标记：x 轴上没有这一天，硬画会落到错误的蜡烛上
  expect(markPoint?.data).toHaveLength(1);
  expect(markPoint?.data?.[0].coord).toEqual([points[5].date, points[5].high]);
  expect(markPoint?.data?.[0].name).toBe("公告+1");
  expect(markPoint?.data?.[0].itemStyle?.color).toBe("#123456");
  // 小圆点 + 默认不出文字：8 个 "公告+1" 的标签在 120 根视窗里必然互相压住
  expect(markPoint?.symbolSize).toBe(6);
  expect(markPoint?.data?.[0].label?.show).toBe(false);
});
