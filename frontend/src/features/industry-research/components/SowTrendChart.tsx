import { useMemo } from "react";
import { EChart } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import type { TrendSeries } from "@/shared/api/industryResearch";

interface Props {
  trend: TrendSeries | undefined;
  height?: number;
}

/** 能繁母猪存栏月度趋势 + 正常保有量参考线（政策锚点由后端按生效日期下发） */
export function SowTrendChart({ trend, height = 296 }: Props) {
  const { colors } = useTheme();
  const option = useMemo(() => {
    if (!trend) return null;
    const label = Object.keys(trend.series)[0];
    const values = trend.series[label] ?? [];

    return {
      animationDuration: 700,
      grid: { left: 52, right: 16, top: 30, bottom: 26 },
      tooltip: {
        trigger: "axis",
        backgroundColor: colors.bgElevated,
        borderColor: colors.border,
        textStyle: { color: colors.textPrimary, fontSize: 12.5 },
        valueFormatter: (v: number | null) => (v !== null && v !== undefined ? `${v.toLocaleString()} 万头` : "—"),
      },
      xAxis: {
        type: "category",
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
          name: label,
          type: "bar",
          data: values,
          barWidth: "62%",
          itemStyle: { borderRadius: [3, 3, 0, 0], color: `${colors.primary}80` },
          markLine:
            trend.reference !== null
              ? {
                  silent: true,
                  symbol: "none",
                  lineStyle: { type: "dashed", color: colors.textSecondary },
                  label: {
                    color: colors.textSecondary,
                    fontSize: 11,
                    position: "insideEndTop",
                    formatter: `${trend.reference.label} ${trend.reference.value.toLocaleString()}`,
                  },
                  data: [{ yAxis: trend.reference.value }],
                }
              : undefined,
        },
      ],
    };
  }, [trend, colors]);

  if (!option) return null;
  return <EChart option={option} height={height} />;
}
