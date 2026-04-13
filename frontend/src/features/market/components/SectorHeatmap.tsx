import ReactECharts from "echarts-for-react";
import { Card } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchSectors } from "@/shared/api/market";

export function SectorHeatmap() {
  const { data = [] } = useQuery({
    queryKey: ["market-sectors"],
    queryFn: fetchSectors,
  });

  const option = {
    tooltip: {
      formatter: (params: any) => {
        const d = params.data;
        return `<b>${d.name}</b><br/>涨跌: ${d.changePercent > 0 ? "+" : ""}${d.changePercent.toFixed(2)}%<br/>市值: ${(d.value / 1e12).toFixed(2)}万亿`;
      },
    },
    series: [
      {
        type: "treemap" as const,
        roam: false,
        breadcrumb: { show: false },
        label: {
          show: true,
          formatter: (params: any) => {
            const d = params.data;
            const sign = d.changePercent > 0 ? "+" : "";
            return `{name|${d.name}}\n{val|${sign}${d.changePercent.toFixed(2)}%}`;
          },
          rich: {
            name: { fontSize: 13, color: "#fff", lineHeight: 20 },
            val: { fontSize: 11, color: "rgba(255,255,255,0.85)", lineHeight: 18 },
          },
        },
        data: data.map((s) => ({
          name: s.name,
          value: s.totalMarketCap,
          changePercent: s.changePercent,
          itemStyle: {
            color: s.changePercent > 1
              ? "#dc2626"
              : s.changePercent > 0
                ? "#f87171"
                : s.changePercent > -1
                  ? "#6b7280"
                  : s.changePercent > -2
                    ? "#4ade80"
                    : "#16a34a",
          },
        })),
      },
    ],
  };

  return (
    <Card title="板块热力图" size="small">
      <ReactECharts option={option} style={{ height: 300 }} />
    </Card>
  );
}
