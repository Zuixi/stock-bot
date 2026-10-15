import { useMemo } from "react";
import { EChart } from "@/shared/ui/EChart";
import { useIsNarrowViewport } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { TrendSeries } from "@/shared/api/industryResearch";

interface Props {
  trend: TrendSeries | undefined;
  height?: number;
}

/** 生猪价格 vs 行业成本（月度），tooltip 折算头均盈亏 */
export function PriceCostChart({ trend, height = 296 }: Props) {
  const { colors } = useTheme();
  /**
   * 窄屏形态（Phase 3 §4.5）：**不隐藏、不换图**，只收版式——
   * - 左轴 44 → 34px（308px 画布里 44px 是 1/7）；
   * - 图例从右上角移到顶部左起（窄屏右上角只有 ~120px，两项图例名会互相压住）；
   * - 图表高度 296 → 240px（月度序列 60+ 点，矮一点反而更易看趋势，
   *   且让首屏在 390px 下能同时看到图与下方的阶段条）。
   */
  const narrow = useIsNarrowViewport();
  const option = useMemo(() => {
    if (!trend) return null;
    const labels = Object.keys(trend.series);
    const priceLabel = labels.find((k) => k.includes("生猪")) ?? labels[0];
    const costLabel = labels.find((k) => k !== priceLabel) ?? labels[1];
    const prices = trend.series[priceLabel] ?? [];
    const costs = trend.series[costLabel] ?? [];

    return {
      animationDuration: 700,
      grid: {
        left: narrow ? 34 : 44,
        right: narrow ? 8 : 16,
        top: narrow ? 44 : 36,
        bottom: narrow ? 22 : 26,
      },
      legend: narrow
        ? {
            top: 2,
            left: 0,
            itemWidth: 12,
            itemHeight: 2.5,
            icon: "rect",
            textStyle: { color: colors.textSecondary, fontSize: 11 },
          }
        : {
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
  }, [trend, colors, narrow]);

  if (!option) return null;
  // 窄屏高度收窄（§4.5）：默认 296 → 240；调用方显式传 `height` 时以调用方为准
  return <EChart option={option} height={narrow && height === 296 ? 240 : height} />;
}
