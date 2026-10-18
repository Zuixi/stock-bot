import { THEME_COLORS, type ThemePalette } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import {
  DEFAULT_TAIL_BARS,
  MA_DEFS,
  NARROW_TAIL_BARS,
  fmtAmount,
  fmtVolume,
  type MaKey,
} from "./klineMath";

export interface KlineOptionInput {
  points: KLinePoint[]; // 聚合后的全量序列
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
  /** 当前主题色板（KlineChart 绕过 EChart 封装直连 ReactECharts，需自带主题色） */
  colors?: ThemePalette;
  /**
   * 窄屏形态（Phase 3 §4.4，`<768` 由调用方按视口断点判定）。
   *
   * 影响三项，**都不改变"画什么"**（全量序列、双 `dataZoom`、MA、成交量一个不少）：
   * - 初始视窗：`NARROW_TAIL_BARS`(30) 而不是 `DEFAULT_TAIL_BARS`(120)，单根蜡烛可辨；
   * - 左轴留白：60 → 46px（308px 画布里 60px 是 1/5 宽度）；
   * - 网格底部：`slider` 占 22px，底部留白随之收窄，避免滑块压住成交量柱。
   */
  narrow?: boolean;
}

const pct = (cur: number, prev: number | undefined) =>
  prev == null || prev === 0 ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

/** tooltip 列式行：灰标签左、语义色数值右（tabular 对齐） */
const row = (label: string, value: string, flat: string, color?: string) =>
  `<div style="display:flex;justify-content:space-between;gap:16px">` +
  `<span style="color:${flat}">${label}</span>` +
  `<span class="num"${color ? ` style="color:${color};font-weight:600"` : ""}>${value}</span>` +
  `</div>`;

/**
 * 轴级 axisPointer（两个 xAxis 共用）。
 *
 * ⚠️ `type` 这里**只能是 `"line"`**（或 `"shadow"`），不能写 `"cross"`：
 * `cross` 只属于 `tooltip.axisPointer` / 顶层 axisPointer。写在这里会被
 * `modelHelper.makeAxisPointerModel` 的模型链（`axis.model.getModel('axisPointer',
 * <tooltip 派生模型>)`，轴级优先级最高）覆盖掉 tooltip 派生出来的 `"line"`，
 * 随后 `CartesianAxisPointer.makeElOption` 执行 `pointerShapeBuilder["cross"](...)`
 * ——该表只有 line/shadow/none ⇒ 抛 `pointerShapeBuilder[axisPointerType] is not a
 * function`（PAGEERROR，整张图的十字光标与 tooltip 全灭，实测 echarts 5.6.0）。
 * 跨 pane 的"cross 形"由顶层 `axisPointer.link` + `tooltip.axisPointer.type` 给出：
 * 竖线贯穿两个 pane（link），横线只在本 pane（tooltip 的 cross）。
 *
 * `snap` 吸附蜡烛中心；`triggerOn` 带 `click` 才有触屏十字（ECharts 实际从顶层
 * axisPointer 读 triggerOn，这里保留以满足轴级语义/可读性，顶层也有同名兜底）。
 */
const AXIS_POINTER_PER_AXIS = {
  type: "line",
  snap: true,
  triggerOn: "mousemove|click",
} as const;

export function buildKlineOption({ points, maSeries, visibleMas, colors, narrow = false }: KlineOptionInput) {
  const c = colors ?? THEME_COLORS.light;
  const dates = points.map((p) => p.date); // 原始 ISO 日期串（tooltip 直接消费）
  const ohlc = points.map((p) => [p.open, p.close, p.low, p.high]);
  const lastClose = points.length ? points[points.length - 1].close : 0;
  // 窄屏只收**初始视窗**根数（§4.4）：数据仍是全量，dataZoom 可漫游/手势缩放
  const tailBars = narrow ? NARROW_TAIL_BARS : DEFAULT_TAIL_BARS;
  const tailStart = Math.max(0, points.length - tailBars);
  // 窄屏左轴留白收窄：308px 画布下 60px 是 1/5 宽度，K 线区被压得太窄
  const axisLeft = narrow ? 46 : 60;

  const tooltipFormatter = (params: unknown): string => {
    const list = params as Array<{ dataIndex: number; seriesType: string; seriesName?: string; value?: unknown }>;
    // ⚠️ **任意 series 定位**，不能只认 candlestick：ECharts 的 axis 触发只收集
    // **当前悬停 axis 上的 series**，悬停成交量 pane（xAxisIndex 1）时 params 里
    // 只有 `成交量` 这条 bar、没有 candlestick ⇒ 旧写法 `find(seriesType ===
    // "candlestick")` 返回 undefined ⇒ 成交量区 tooltip 空白（用户报的 F5）。
    // `dataIndex` 在所有 axis 上语义相同，故取 `list[0]` 即可，OHLC 一律回查 points。
    const i = list[0]?.dataIndex;
    if (i == null || !points[i]) return "";
    const p = points[i];
    const { open, close, low, high } = p;
    const prev = i > 0 ? points[i - 1].close : undefined;
    // A股习惯：开/收/高/低/涨跌额/涨跌幅 随当日涨跌着色（首日中性）
    const color = prev == null ? c.flat : close >= prev ? c.up : c.down;
    const change = pct(close, prev);
    const diff = prev == null ? "--" : signed(close - prev);
    const maRow = (name: MaKey) => {
      const def = MA_DEFS.find((d) => d.key === name);
      const v = maSeries[name]?.[i];
      return v == null ? "" : row(name, v.toFixed(2), c.flat, def?.color);
    };
    return `<div style="font-size:12px;line-height:1.8;min-width:150px">
      <div style="font-weight:600;margin-bottom:2px">${p.date}</div>
      ${row("开盘", open.toFixed(2), c.flat, color)}
      ${row("收盘", close.toFixed(2), c.flat, color)}
      ${row("最高", high.toFixed(2), c.flat, color)}
      ${row("最低", low.toFixed(2), c.flat, color)}
      ${row("涨跌额", diff, c.flat, color)}
      ${row("涨跌幅", change, c.flat, color)}
      ${row("成交量", fmtVolume(p.volume), c.flat)}
      ${row("成交额", fmtAmount(p.amount), c.flat)}
      ${visibleMas.map((k) => maRow(k)).join("")}
    </div>`;
  };

  return {
    textStyle: { color: c.textSecondary },
    // 顶层 axisPointer 是跨 pane 联动的**唯一**开关（F4）：`link.xAxisIndex: "all"`
    // 把两个 grid 的 x 轴绑成一条十字光标 —— 竖线同时贯穿 K 线 pane 与成交量 pane，
    // 横线只留在当前 pane（ECharts cross 的语义）。缺这一行就只剩当前 pane 内的十字。
    axisPointer: {
      link: [{ xAxisIndex: "all" as const }],
      lineStyle: { color: c.flat, type: "dashed" as const, width: 1 },
      label: { backgroundColor: c.textPrimary, color: c.bgPage, fontSize: 10 },
      // 触屏点按也出十字（ECharts 的 triggerOn 只认顶层/global，轴级那份是文档性的）
      triggerOn: "mousemove|click" as const,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "cross" as const },
      formatter: tooltipFormatter,
      // 窄屏（308px）下 tooltip 默认会溢出视口被裁掉；confine 关进画布内（§5.1）
      ...(narrow ? { confine: true } : {}),
    },
    legend: { show: false },
    grid: [
      { left: axisLeft, right: narrow ? 12 : 20, top: 28, height: "50%" },
      { left: axisLeft, right: narrow ? 12 : 20, top: "68%", height: "16%" },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { onZero: false, lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 0,
        axisPointer: AXIS_POINTER_PER_AXIS,
      },
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 1,
        axisPointer: AXIS_POINTER_PER_AXIS,
      },
    ],
    yAxis: [
      {
        scale: true,
        gridIndex: 0,
        axisLabel: { color: c.textSecondary },
        splitLine: { lineStyle: { type: "dashed", color: c.border } },
      },
      {
        scale: true,
        gridIndex: 1,
        splitNumber: 2,
        axisLabel: { show: false },
        splitLine: { show: false },
      },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1], startValue: tailStart, endValue: points.length - 1 },
      {
        type: "slider",
        xAxisIndex: [0, 1],
        height: 16,
        bottom: 6,
        startValue: tailStart,
        endValue: points.length - 1,
        borderColor: c.border,
        textStyle: { color: c.textSecondary },
        labelFormatter: (v: unknown) => String(v),
      },
    ],
    series: [
      {
        name: "K线",
        type: "candlestick",
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: { color: c.up, color0: c.down, borderColor: c.up, borderColor0: c.down },
        markLine: {
          symbol: "none",
          silent: true,
          lineStyle: { type: "dashed", color: c.flat, width: 1 },
          label: { formatter: () => lastClose.toFixed(2), position: "insideEndTop", fontSize: 10, color: c.flat },
          data: [{ yAxis: lastClose }],
        },
      },
      ...MA_DEFS.filter((d) => visibleMas.includes(d.key)).map((d) => ({
        name: d.key,
        type: "line",
        data: maSeries[d.key] ?? [],
        xAxisIndex: 0,
        yAxisIndex: 0,
        symbol: "none",
        smooth: false,
        lineStyle: { width: 1, color: d.color },
        itemStyle: { color: d.color },
        emphasis: { disabled: true },
      })),
      {
        name: "成交量",
        type: "bar",
        data: points.map((p) => ({
          value: p.volume,
          itemStyle: { color: p.close >= p.open ? c.up : c.down },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 8,
      },
    ],
  };
}
