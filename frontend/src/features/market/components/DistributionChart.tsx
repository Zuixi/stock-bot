import ReactECharts from "echarts-for-react";
import { Card, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchDistribution } from "@/shared/api/market";
import { COLORS } from "@/app/theme";

const STALE_TIME = 5 * 60 * 1000;

export function DistributionChart() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-distribution"],
    queryFn: fetchDistribution,
    staleTime: STALE_TIME,
  });

  const option = {
    tooltip: {
      trigger: "axis" as const,
      formatter: (params: any) => {
        const p = params[0];
        return `${p.name}<br/>数量: <b>${p.value}</b>`;
      },
    },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: {
      type: "category" as const,
      data: data.map((d) => d.range),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: { type: "value" as const, splitLine: { lineStyle: { type: "dashed" as const } } },
    series: [
      {
        type: "bar" as const,
        data: data.map((d) => ({
          value: d.count,
          itemStyle: {
            color: d.range.includes("跌") || d.range.startsWith(">-") || d.range.startsWith("-")
              ? COLORS.down
              : d.range.includes("涨") || d.range.startsWith(">5") || d.range.startsWith("1") || d.range.startsWith("3")
                ? COLORS.up
                : d.range.startsWith("0~1")
                  ? "#f97316"
                : "#94a3b8",
          },
        })),
        barMaxWidth: 36,
      },
    ],
  };

  return (
    <Card title="A股涨跌分布" size="small">
      <Spin spinning={isLoading}>
        <ReactECharts option={option} style={{ height: 260 }} />
      </Spin>
    </Card>
  );
}
