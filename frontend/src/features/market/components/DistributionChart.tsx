import ReactECharts from "echarts-for-react";
import { Card } from "antd";
import { MOCK_DISTRIBUTION } from "@/shared/mocks/market";
import { COLORS } from "@/app/theme";

export function DistributionChart() {
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
      data: MOCK_DISTRIBUTION.map((d) => d.range),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: { type: "value" as const, splitLine: { lineStyle: { type: "dashed" as const } } },
    series: [
      {
        type: "bar" as const,
        data: MOCK_DISTRIBUTION.map((d) => ({
          value: d.count,
          itemStyle: {
            color: d.range.includes("跌") || d.range.startsWith(">-") || d.range.startsWith("-")
              ? COLORS.down
              : d.range.includes("涨") || d.range.startsWith(">5") || d.range.startsWith("1") || d.range.startsWith("3")
                ? COLORS.up
                : "#94a3b8",
          },
        })),
        barMaxWidth: 36,
      },
    ],
  };

  return (
    <Card title="A股涨跌分布" size="small">
      <ReactECharts option={option} style={{ height: 260 }} />
    </Card>
  );
}
