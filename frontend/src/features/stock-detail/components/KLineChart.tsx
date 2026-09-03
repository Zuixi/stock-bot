import ReactECharts from "echarts-for-react";
import { Card, Empty, Segmented, Spin } from "antd";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { COLORS } from "@/app/theme";
import { fetchKlineBySymbol } from "@/shared/api/quotes";

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
  const { data = [], isLoading } = useQuery({
    queryKey: ["kline", symbol, range],
    queryFn: () => fetchKlineBySymbol(symbol, range),
    enabled: Boolean(symbol),
  });

  const dates = useMemo(() => data.map((d) => d.date), [data]);
  const ohlc = useMemo(() => data.map((d) => [d.open, d.close, d.low, d.high]), [data]);
  const volumes = useMemo(
    () =>
      data.map((d) => ({
        value: d.volume,
        itemStyle: { color: d.close >= d.open ? COLORS.up : COLORS.down },
      })),
    [data]
  );

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
    // 不设 start/end：周期切换后默认展示所选区间全量数据，滚轮缩放由用户主动控制
    dataZoom: [
      { type: "inside" as const, xAxisIndex: [0, 1] },
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
      {isLoading ? (
        <div style={{ height: 380, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Spin />
        </div>
      ) : data.length > 0 ? (
        <ReactECharts option={option} notMerge style={{ height: 380 }} />
      ) : (
        <div style={{ height: 380, display: "flex", alignItems: "center", justifyContent: "center" }}>
          <Empty description="暂无K线数据" />
        </div>
      )}
    </Card>
  );
}
