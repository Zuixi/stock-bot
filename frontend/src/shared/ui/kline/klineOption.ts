import { COLORS } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import { DEFAULT_TAIL_BARS, MA_DEFS, fmtAmount, fmtVolume, type MaKey } from "./klineMath";

export interface KlineOptionInput {
  points: KLinePoint[]; // 聚合后的全量序列
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
}

const pct = (cur: number, prev: number | undefined) =>
  prev == null || prev === 0 ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;

const signed = (v: number) => `${v >= 0 ? "+" : ""}${v.toFixed(2)}`;

/** tooltip 列式行：灰标签左、语义色数值右（tabular 对齐） */
const row = (label: string, value: string, color?: string) =>
  `<div style="display:flex;justify-content:space-between;gap:16px">` +
  `<span style="color:${COLORS.flat}">${label}</span>` +
  `<span style="font-variant-numeric:tabular-nums${color ? `;color:${color};font-weight:600` : ""}">${value}</span>` +
  `</div>`;

export function buildKlineOption({ points, maSeries, visibleMas }: KlineOptionInput) {
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
    const color = prev == null ? COLORS.flat : close >= prev ? COLORS.up : COLORS.down;
    const change = pct(close, prev);
    const diff = prev == null ? "--" : signed(close - prev);
    const maRow = (name: MaKey) => {
      const def = MA_DEFS.find((d) => d.key === name);
      const v = maSeries[name]?.[i];
      return v == null ? "" : row(name, v.toFixed(2), def?.color);
    };
    return `<div style="font-size:12px;line-height:1.8;min-width:150px">
      <div style="font-weight:600;margin-bottom:2px">${p.date}</div>
      ${row("开盘", open.toFixed(2), color)}
      ${row("收盘", close.toFixed(2), color)}
      ${row("最高", high.toFixed(2), color)}
      ${row("最低", low.toFixed(2), color)}
      ${row("涨跌额", diff, color)}
      ${row("涨跌幅", change, color)}
      ${row("成交量", fmtVolume(p.volume))}
      ${row("成交额", fmtAmount(p.amount))}
      ${visibleMas.map((k) => maRow(k)).join("")}
    </div>`;
  };

  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, formatter: tooltipFormatter },
    legend: { show: false },
    grid: [
      { left: 60, right: 20, top: 28, height: "50%" },
      { left: 60, right: 20, top: "68%", height: "16%" },
    ],
    xAxis: [
      { type: "category", data: dates, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
      { type: "category", data: dates, boundaryGap: true, gridIndex: 1, axisLabel: { show: false } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { type: "dashed" } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
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
        itemStyle: { color: COLORS.up, color0: COLORS.down, borderColor: COLORS.up, borderColor0: COLORS.down },
        markLine: {
          symbol: "none",
          silent: true,
          lineStyle: { type: "dashed", color: COLORS.flat, width: 1 },
          label: { formatter: () => lastClose.toFixed(2), position: "insideEndTop", fontSize: 10, color: COLORS.flat },
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
          itemStyle: { color: p.close >= p.open ? COLORS.up : COLORS.down },
        })),
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 8,
      },
    ],
  };
}
