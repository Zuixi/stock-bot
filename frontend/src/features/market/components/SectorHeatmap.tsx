import ReactECharts from "echarts-for-react";
import { Card, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchSectors } from "@/shared/api/market";
import { hexLerp } from "./DistributionChart";

const STALE_TIME = 5 * 60 * 1000;

/** 连续色阶：0%→灰，±5%→深绿/深红（Finviz 式梯度，替代离散 5 档）。 */
function heatColor(pct: number): string {
  const t = Math.min(1, Math.abs(pct) / 5);
  return pct >= 0 ? hexLerp("#d1d5db", "#dc2626", t) : hexLerp("#d1d5db", "#16a34a", t);
}

export function SectorHeatmap() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-sectors"],
    queryFn: fetchSectors,
    staleTime: STALE_TIME,
  });

  const option = {
    tooltip: {
      formatter: (params: any) => {
        const d = params.data;
        const sign = d.changePercent > 0 ? "+" : "";
        const leaders = (d.topStocks ?? [])
          .slice(0, 2)
          .map((s: { name: string; changePercent: number }) => `${s.name} ${s.changePercent > 0 ? "+" : ""}${s.changePercent.toFixed(2)}%`)
          .join("、");
        return (
          `<b>${d.name}</b><br/>` +
          `涨跌: ${sign}${d.changePercent.toFixed(2)}%<br/>` +
          `市值: ${(d.value / 1e12).toFixed(2)}万亿 · ${d.stockCount ?? "—"} 只<br/>` +
          (leaders ? `领涨: ${leaders}` : "")
        );
      },
    },
    series: [
      {
        type: "treemap" as const,
        roam: false,
        breadcrumb: { show: false },
        nodeClick: false,
        label: {
          show: true,
          formatter: (params: any) => {
            const d = params.data;
            const sign = d.changePercent > 0 ? "+" : "";
            // 浅色块（|涨跌|<0.8%）用深字保证对比度
            const cls = Math.abs(d.changePercent) < 0.8 ? "Dark" : "";
            return `{name${cls}|${d.name}}\n{val${cls}|${sign}${d.changePercent.toFixed(2)}%}`;
          },
          rich: {
            name: { fontSize: 13, color: "#fff", lineHeight: 20 },
            val: { fontSize: 11, color: "rgba(255,255,255,0.85)", lineHeight: 18 },
            nameDark: { fontSize: 13, color: "#374151", lineHeight: 20 },
            valDark: { fontSize: 11, color: "#6b7280", lineHeight: 18 },
          },
        },
        data: data.map((s) => ({
          name: s.name,
          value: s.totalMarketCap,
          changePercent: s.changePercent,
          stockCount: s.stockCount,
          topStocks: s.topStocks,
          itemStyle: { color: heatColor(s.changePercent) },
        })),
      },
    ],
  };

  return (
    <Card
      title="板块热力图"
      size="small"
      extra={
        <span
          onClick={() => navigate("/market/hot-sectors/industry")}
          style={{ fontSize: 12, color: "#1677ff", cursor: "pointer" }}
        >
          查看全部 ›
        </span>
      }
    >
      <Spin spinning={isLoading}>
        <ReactECharts
          option={option}
          style={{ height: 300 }}
          onEvents={{
            click: (params: { data?: { name?: string } }) => {
              if (params.data?.name) {
                navigate(`/market/hot-sectors/industry?board=${encodeURIComponent(params.data.name)}`);
              }
            },
          }}
        />
      </Spin>
    </Card>
  );
}
