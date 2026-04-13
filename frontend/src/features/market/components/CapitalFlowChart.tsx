import ReactECharts from "echarts-for-react";
import { Card } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchCapitalFlow } from "@/shared/api/market";

export function CapitalFlowChart() {
  const { data = [] } = useQuery({
    queryKey: ["market-capital-flow"],
    queryFn: fetchCapitalFlow,
  });
  const names = data.map((d) => d.name);
  const inflows = data.map((d) => d.inflow);
  const outflows = data.map((d) => d.outflow);

  const option = {
    tooltip: {
      trigger: "axis" as const,
      axisPointer: { type: "shadow" as const },
      formatter: (params: any) => {
        const title = params[0].name;
        return `${title}<br/>` + params.map((p: any) => `${p.seriesName}: ${p.value}亿`).join("<br/>");
      },
    },
    legend: { data: ["主力流入", "主力流出"], top: 0 },
    grid: { left: 50, right: 16, top: 32, bottom: 32 },
    xAxis: { type: "category" as const, data: names, axisLabel: { fontSize: 11 } },
    yAxis: {
      type: "value" as const,
      axisLabel: { formatter: "{value}亿" },
      splitLine: { lineStyle: { type: "dashed" as const } },
    },
    series: [
      { name: "主力流入", type: "bar" as const, stack: "flow", data: inflows, itemStyle: { color: "#ef4444" }, barMaxWidth: 28 },
      { name: "主力流出", type: "bar" as const, stack: "flow", data: outflows, itemStyle: { color: "#22c55e" }, barMaxWidth: 28 },
    ],
  };

  return (
    <Card title="A股主力净流入" size="small">
      <ReactECharts option={option} style={{ height: 260 }} />
    </Card>
  );
}
