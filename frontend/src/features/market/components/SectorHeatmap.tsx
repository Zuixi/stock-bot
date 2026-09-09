import { Card, Spin } from "antd";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchSectors } from "@/shared/api/market";
import { hexLerp } from "./DistributionChart";
import { EChart } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";

const STALE_TIME = 5 * 60 * 1000;

/** 连续色阶：0%→中性，±5%→深红/深绿（Finviz 式梯度，端点色随主题，Stage C） */
function heatColor(pct: number, c: ThemePalette): string {
  const t = Math.min(1, Math.abs(pct) / 5);
  return pct >= 0 ? hexLerp(c.border, c.up, t) : hexLerp(c.border, c.down, t);
}

export function SectorHeatmap() {
  const navigate = useNavigate();
  const { colors } = useTheme();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-sectors"],
    queryFn: fetchSectors,
    staleTime: STALE_TIME,
  });

  const option = {
    tooltip: {
      formatter: (params: any) => {
        const d = params?.data;
        // 空数据/内部虚拟节点可能缺少字段，缺值一律降级展示
        if (!d || d.changePercent == null || d.name == null) return "";
        const sign = d.changePercent > 0 ? "+" : "";
        const leaders = (d.topStocks ?? [])
          .slice(0, 2)
          .map((s: { name: string; changePercent: number }) =>
            s.changePercent == null
              ? s.name
              : `${s.name} ${s.changePercent > 0 ? "+" : ""}${s.changePercent.toFixed(2)}%`
          )
          .join("、");
        return (
          `<b>${d.name}</b><br/>` +
          `涨跌: ${sign}${d.changePercent.toFixed(2)}%<br/>` +
          `市值: ${d.value == null ? "—" : (d.value / 1e12).toFixed(2)}万亿 · ${d.stockCount ?? "—"} 只<br/>` +
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
            const d = params?.data;
            // 空库时 ECharts 会对内部虚拟节点执行一次 label 渲染，字段可能缺失
            if (!d || d.changePercent == null || d.name == null) return "";
            const sign = d.changePercent > 0 ? "+" : "";
            // 近中性浅块（|涨跌|<0.8%）用主题主文本色保证对比度；暗色下中性块为深色，同色自然成立
            const cls = Math.abs(d.changePercent) < 0.8 ? "Dark" : "";
            return `{name${cls}|${d.name}}\n{val${cls}|${sign}${d.changePercent.toFixed(2)}%}`;
          },
          rich: {
            name: { fontSize: 13, color: "#fff", lineHeight: 20 },
            val: { fontSize: 11, color: "rgba(255,255,255,0.85)", lineHeight: 18 },
            nameDark: { fontSize: 13, color: colors.textPrimary, lineHeight: 20 },
            valDark: { fontSize: 11, color: colors.textSecondary, lineHeight: 18 },
          },
        },
        data: data.map((s) => ({
          name: s.name,
          value: s.totalMarketCap,
          changePercent: s.changePercent,
          stockCount: s.stockCount,
          topStocks: s.topStocks,
          itemStyle: { color: heatColor(s.changePercent, colors) },
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
          style={{ fontSize: 12, color: colors.accent, cursor: "pointer" }}
        >
          查看全部 ›
        </span>
      }
    >
      <Spin spinning={isLoading}>
        <EChart
          option={option}
          height={300}
          onEvents={{
            click: (params: unknown) => {
              const data = (params as { data?: { name?: string } }).data;
              if (data?.name) {
                navigate(`/market/hot-sectors/industry?board=${encodeURIComponent(data.name)}`);
              }
            },
          }}
        />
      </Spin>
    </Card>
  );
}
