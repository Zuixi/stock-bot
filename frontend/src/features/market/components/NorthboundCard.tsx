import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { fetchNorthbound } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtNorthYi } from "./format";

const STALE_TIME = 5 * 60 * 1000;

function buildOption(points: Array<{ date: string; netAmount: number | null }>) {
  const dates = points.map((p) => p.date.slice(5));
  const values = points.map((p) => (p.netAmount == null ? null : p.netAmount / 1e4));
  return {
    grid: { left: 8, right: 16, top: 24, bottom: 8, containLabel: true },
    tooltip: { trigger: "axis", valueFormatter: (v: number | null) => (v == null ? "—" : `${v.toFixed(2)}亿`) },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` }, splitLine: { lineStyle: { color: "#f0f0f0" } } },
    series: [
      {
        type: "line",
        data: values,
        symbol: "circle",
        symbolSize: 4,
        connectNulls: true,
        lineStyle: { width: 2, color: COLORS.primary },
        itemStyle: { color: COLORS.primary },
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#c9cdd4", type: "dashed" },
          data: [{ yAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  };
}

export function NorthboundCard() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["northbound", 30],
    queryFn: () => fetchNorthbound(30),
    staleTime: STALE_TIME,
  });
  const last = data.length > 0 ? data[data.length - 1] : undefined;
  const total = data.reduce((acc, p) => acc + (p.netAmount ?? 0), 0);
  const lastColor = (last?.netAmount ?? 0) > 0 ? COLORS.up : (last?.netAmount ?? 0) < 0 ? COLORS.down : COLORS.flat;
  const totalColor = total > 0 ? COLORS.up : total < 0 ? COLORS.down : COLORS.flat;
  return (
    <Card
      title="北向资金"
      size="small"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>盘后净流入 · 亿元</Typography.Text>}
    >
      <Spin spinning={isLoading}>
        <div style={{ display: "flex", gap: 24, marginBottom: 4, fontSize: 12 }}>
          <span>当日 <b style={{ color: lastColor, fontSize: 16 }}>{fmtNorthYi(last?.netAmount)}</b></span>
          <span>近30日累计 <b style={{ color: totalColor }}>{fmtNorthYi(total)}</b></span>
        </div>
        {data.length > 0 ? (
          <ReactECharts option={buildOption(data)} notMerge lazyUpdate style={{ height: 216 }} />
        ) : (
          <div style={{ height: 216, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.flat }}>
            暂无北向数据（盘后自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
