import ReactECharts from "echarts-for-react";
import { Card, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchSectors } from "@/shared/api/market";

const STALE_TIME = 5 * 60 * 1000;

export function SectorHeatmap() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-sectors"],
    queryFn: fetchSectors,
    staleTime: STALE_TIME,
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
      <Spin spinning={isLoading}>
        <ReactECharts option={option} style={{ height: 300 }} />
      </Spin>
    </Card>
  );
}
