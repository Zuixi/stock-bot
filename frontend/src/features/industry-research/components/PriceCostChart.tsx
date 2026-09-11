import { useMemo } from "react";
import { EChart } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import type { TrendSeries } from "@/shared/api/industryResearch";

interface Props {
  trend: TrendSeries | undefined;
  height?: number;
}

/** 生猪价格 vs 行业成本（月度），tooltip 折算头均盈亏 */
export function PriceCostChart({ trend, height = 296 }: Props) {
  const { colors } = useTheme();
  const option = useMemo(() => {
    if (!trend) return null;
    const labels = Object.keys(trend.series);
    const priceLabel = labels.find((k) => k.includes("生猪")) ?? labels[0];
    const costLabel = labels.find((k) => k !== priceLabel) ?? labels[1];
    const prices = trend.series[priceLabel] ?? [];
    const costs = trend.series[costLabel] ?? [];

    return {
      animationDuration: 700,
      grid: { left: 44, right: 16, top: 36, bottom: 26 },
      legend: {
        top: 2,
        right: 8,
        itemWidth: 14,
        itemHeight: 2.5,
        icon: "rect",
        textStyle: { color: colors.textSecondary, fontSize: 12 },
      },
      tooltip: {
        trigger: "axis",
        backgroundColor: colors.bgElevated,
        borderColor: colors.border,
        textStyle: { color: colors.textPrimary, fontSize: 12.5 },
        formatter: (params: unknown) => {
          const list = params as { axisValue: string; value: number | null; seriesName: string; color: string }[];
          const price = list[0]?.value;
          const cost = list[1]?.value;
          const diff = price !== null && cost !== null && price !== undefined && cost !== undefined ? price - cost : null;
          const diffLine =
            diff === null
              ? ""
              : `<br/><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${diff >= 0 ? colors.up : colors.down};margin-right:6px"></span>头均盈亏 ≈ <b style="color:${diff >= 0 ? colors.up : colors.down}">${diff >= 0 ? "+" : ""}${(diff * 115).toFixed(0)}</b> 元/头`;
          return (
            `<b>${list[0]?.axisValue ?? ""}</b>` +
            list
              .map(
                (p) =>
                  `<br/><span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:${p.color};margin-right:6px"></span>${p.seriesName} <b>${p.value?.toFixed(2) ?? "—"}</b> 元/kg`
              )
              .join("") +
            diffLine
          );
        },
      },
      xAxis: {
        type: "category",
        boundaryGap: false,
        data: trend.periods,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { fontSize: 11, interval: 5 },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLine: { show: false },
        axisTick: { show: false },
        axisLabel: { fontSize: 11 },
      },
      series: [
        {
          name: priceLabel,
          type: "line",
          smooth: 0.3,
          symbol: "none",
          data: prices,
          lineStyle: { width: 2.5, color: colors.primary },
          itemStyle: { color: colors.primary },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: `${colors.primary}1a` },
                { offset: 1, color: `${colors.primary}00` },
              ],
            },
          },
          markPoint: {
            data: [{ type: "min", name: "周期低点" }],
            symbol: "pin",
            symbolSize: 44,
            itemStyle: { color: colors.up },
            label: { color: "#fff", fontSize: 10, formatter: (p: { value: number }) => p.value?.toFixed(1) },
          },
        },
        {
          name: costLabel,
          type: "line",
          smooth: 0.3,
          symbol: "none",
          data: costs,
          lineStyle: { width: 2, type: [4, 3], color: "#fa8c16" },
          itemStyle: { color: "#fa8c16" },
        },
      ],
    };
  }, [trend, colors]);

  if (!option) return null;
  return <EChart option={option} height={height} />;
}
