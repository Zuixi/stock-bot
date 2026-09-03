import { COLORS } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";
import { MA_DEFS, buildAxisLabels, fmtAmount, fmtVolume, type MaKey } from "./klineMath";

export interface KlineOptionInput {
  points: KLinePoint[];
  maSeries: Partial<Record<MaKey, (number | null)[]>>;
  visibleMas: MaKey[];
}

const pct = (cur: number, prev: number | undefined) =>
  prev == null ? "--" : `${(((cur - prev) / prev) * 100).toFixed(2)}%`;

export function buildKlineOption({ points, maSeries, visibleMas }: KlineOptionInput) {
  const dates = points.map((p) => p.date);
  const axisLabels = buildAxisLabels(dates);
  const ohlc = points.map((p) => [p.open, p.close, p.low, p.high]);
  const lastClose = points.length ? points[points.length - 1].close : 0;

  const tooltipFormatter = (params: unknown): string => {
    const list = params as Array<{ dataIndex: number; seriesType: string; seriesName?: string; value?: unknown }>;
    const candle = list.find((p) => p.seriesType === "candlestick");
    if (!candle || !points[candle.dataIndex]) return "";
    const i = candle.dataIndex;
    const p = points[i];
    const [open, close, low, high] = candle.value as number[];
    const prev = i > 0 ? points[i - 1].close : undefined;
    const change = pct(close, prev);
    const color = prev == null ? COLORS.flat : close >= prev ? COLORS.up : COLORS.down;
    const maLine = (name: MaKey) => {
      const def = MA_DEFS.find((d) => d.key === name);
      const v = maSeries[name]?.[i];
      return v == null ? "" : `<span style="margin-left:8px;color:${def?.color}">${name} ${v.toFixed(2)}</span>`;
    };
    return `<div style="font-size:12px;line-height:1.9">
      <div style="font-weight:600">${p.date}</div>
      <div>开：<b>${open.toFixed(2)}</b>　高：<b>${high.toFixed(2)}</b>　低：<b>${low.toFixed(2)}</b>　收：<b>${close.toFixed(2)}</b></div>
      <div>涨跌幅：<b style="color:${color}">${change}</b>　成交量：${fmtVolume(p.volume)}　成交额：${fmtAmount(p.amount)}</div>
      <div>${visibleMas.map((k) => maLine(k)).join("")}</div>
    </div>`;
  };

  return {
    tooltip: { trigger: "axis", axisPointer: { type: "cross" }, formatter: tooltipFormatter },
    legend: { show: false },
    grid: [
      { left: 60, right: 20, top: 28, height: "50%" },
      { left: 60, right: 20, top: "66%", height: "13%" },
    ],
    xAxis: [
      { type: "category", data: axisLabels, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
      { type: "category", data: axisLabels, boundaryGap: true, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { type: "dashed" } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside", xAxisIndex: [0, 1] },
      { type: "slider", xAxisIndex: [0, 1], height: 16, bottom: 6, start: 0, end: 100 },
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
