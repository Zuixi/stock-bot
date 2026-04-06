import ReactECharts from "echarts-for-react";
import { Card, Segmented } from "antd";
import { useState, useMemo } from "react";
import { generateKLine } from "@/shared/mocks/stocks";
import { COLORS } from "@/app/theme";
import type { KLinePoint } from "@/shared/types";

const RANGES = [
  { label: "1月", value: 30 },
  { label: "3月", value: 90 },
  { label: "6月", value: 180 },
  { label: "1年", value: 365 },
] as const;

interface Props {
  symbol: string;
}

export function KLineChart({ symbol }: Props) {
  const [range, setRange] = useState<number>(90);
  const data = useMemo(() => generateKLine(range), [symbol, range]);

  const dates = data.map((d) => d.date);
  const ohlc = data.map((d) => [d.open, d.close, d.low, d.high]);
  const volumes = data.map((d) => ({
    value: d.volume,
    itemStyle: { color: d.close >= d.open ? COLORS.up : COLORS.down },
  }));

  const option = {
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "cross" as const },
    },
    grid: [
      { left: 60, right: 20, top: 20, height: "55%" },
      { left: 60, right: 20, top: "78%", height: "16%" },
    ],
    xAxis: [
      { type: "category" as const, data: dates, boundaryGap: true, axisLine: { onZero: false }, gridIndex: 0, axisLabel: { show: false } },
      { type: "category" as const, data: dates, boundaryGap: true, gridIndex: 1, axisLabel: { fontSize: 10 } },
    ],
    yAxis: [
      { scale: true, gridIndex: 0, splitLine: { lineStyle: { type: "dashed" as const } } },
      { scale: true, gridIndex: 1, splitNumber: 2, axisLabel: { show: false }, splitLine: { show: false } },
    ],
    dataZoom: [
      { type: "inside" as const, xAxisIndex: [0, 1], start: 60, end: 100 },
    ],
    series: [
      {
        type: "candlestick" as const,
        data: ohlc,
        xAxisIndex: 0,
        yAxisIndex: 0,
        itemStyle: {
          color: COLORS.up,
          color0: COLORS.down,
          borderColor: COLORS.up,
          borderColor0: COLORS.down,
        },
      },
      {
        type: "bar" as const,
        data: volumes,
        xAxisIndex: 1,
        yAxisIndex: 1,
        barMaxWidth: 8,
      },
    ],
  };

  return (
    <Card
      title="历史行情"
      size="small"
      extra={
        <Segmented
          size="small"
          options={RANGES.map((r) => ({ label: r.label, value: r.value }))}
          value={range}
          onChange={(v) => setRange(v as number)}
        />
      }
    >
      <ReactECharts option={option} style={{ height: 380 }} />
    </Card>
  );
}
