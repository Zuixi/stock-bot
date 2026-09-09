import { THEME_COLORS, type ThemePalette } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import { DEFAULT_TAIL_BARS, MA_DEFS, fmtAmount, fmtVolume, type MaKey } from "./klineMath";

export interface KlineOptionInput {
  points: KLinePoint[]; // 聚合后的全量序列
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
  /** 当前主题色板（KlineChart 绕过 EChart 封装直连 ReactECharts，需自带主题色） */
  colors?: ThemePalette;
}

const pct = (cur: number, prev: number | undefined) =>
  prev == null || prev === 0 ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

/** tooltip 列式行：灰标签左、语义色数值右（tabular 对齐） */
const row = (label: string, value: string, flat: string, color?: string) =>
  `<div style="display:flex;justify-content:space-between;gap:16px">` +
  `<span style="color:${flat}">${label}</span>` +
  `<span style="font-variant-numeric:tabular-nums${color ? `;color:${color};font-weight:600` : ""}">${value}</span>` +
  `</div>`;

export function buildKlineOption({ points, maSeries, visibleMas, colors }: KlineOptionInput) {
  const c = colors ?? THEME_COLORS.light;
  const dates = points.map((p) => p.date); // 原始 ISO 日期串（tooltip 直接消费）
  const ohlc = points.map((p) => [p.open, p.close, p.low, p.high]);
  const lastClose = points.length ? points[points.length - 1].close : 0;
  const tailStart = Math.max(0, points.length - DEFAULT_TAIL_BARS);

  const tooltipFormatter = (params: unknown): string => {
    const list = params as Array<{ dataIndex: number; seriesType: string; seriesName?: string; value?: unknown }>;
    const candle = list.find((p) => p.seriesType === "candlestick");
    if (!candle || !points[candle.dataIndex]) return "";
    const i = candle.dataIndex;
    const p = points[i];
    const [open, close, low, high] = candle.value as number[];
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
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, formatter: tooltipFormatter },
    legend: { show: false },
    grid: [
      { left: 60, right: 20, top: 28, height: "50%" },
      { left: 60, right: 20, top: "68%", height: "16%" },
    ],
    xAxis: [
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { onZero: false, lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 0,
      },
      {
        type: "category",
        data: dates,
        boundaryGap: true,
        axisLine: { lineStyle: { color: c.border } },
        axisLabel: { show: false },
        gridIndex: 1,
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
