import { useMemo } from "react";
import { useElementWidth } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { TrendSeries } from "@/shared/api/industryResearch";

interface Props {
  trend: TrendSeries | undefined;
  /** 展示的最近期数（默认 24 个月） */
  months?: number;
}

/** 头均盈亏折算系数：与 `PriceCostChart` 的 tooltip 同一口径（1 元/kg ≈ 115 元/头）。 */
const PER_HEAD = 115;

/**
 * **价格 vs 成本剪刀差**（Phase 4 §5.6）——左栏 `猪周期阶段定位` 下方的小图。
 *
 * ## 为什么是"补一个环"而不是"加一个功能"
 *
 * `industry/workbench.tsx` 是两栏栅格：左栏 `lg={14}`（阶段条 / 价格成本图 / 能繁存栏），
 * 右栏 `lg={10}`（信号面板 / 仓位建议 / 指标速览）。实测右栏第 1 项（信号面板）比左栏
 * 第 1 项（阶段条）高约 400px，于是左栏第 2 项之前有一段约 400px 的空洞。
 *
 * 补的内容**必须已经在 payload 里**（否则就是"顺手加功能"）：
 * `dashboard.trends.price_vs_cost` 正是 `PriceCostChart` 用的那一份
 * （`series[生猪价] - series[成本]`），本组件只做**差分渲染**，不请求任何新数据。
 *
 * ## 为什么不用 ECharts
 *
 * 这是 150px 高、只有一条线的"剪刀差"示意——引入第二个 EChart 实例（含 resize 观察）
 * 的成本高于收益，且它要与上方那张 ECharts 折线**视觉上区分开**（上方是两条线，
 * 这里是一条差值的填充区域）。故用最朴素的 SVG，点数按**容器实测宽度**取
 * （`useElementWidth`，与 §0.1 B2 的"按容器退化"同一口径，不用视口断点）。
 */
export function CostScissorsChart({ trend, months = 24 }: Props) {
  const { colors } = useTheme();
  const { ref, width } = useElementWidth<HTMLDivElement>();

  const model = useMemo(() => {
    if (!trend) return null;
    const labels = Object.keys(trend.series);
    const priceLabel = labels.find((k) => k.includes("生猪")) ?? labels[0];
    const costLabel = labels.find((k) => k !== priceLabel) ?? labels[1];
    const prices = trend.series[priceLabel] ?? [];
    const costs = trend.series[costLabel] ?? [];
    const periods = trend.periods;

    const sliced: Array<{ period: string; gap: number }> = [];
    for (let i = Math.max(0, periods.length - months); i < periods.length; i += 1) {
      const p = prices[i];
      const c = costs[i];
      if (typeof p === "number" && typeof c === "number") {
        sliced.push({ period: periods[i], gap: p - c });
      }
    }
    if (sliced.length < 2) return null;

    const last = sliced[sliced.length - 1];
    const gaps = sliced.map((s) => s.gap);
    const maxAbs = Math.max(0.5, ...gaps.map((g) => Math.abs(g)));
    return { sliced, last, maxAbs, priceLabel, costLabel };
  }, [trend, months]);

  const W = Math.max(160, width || 320);
  const H = 96;
  const PAD = 4;

  if (!model) return null;
  const { sliced, last, maxAbs } = model;
  const x = (i: number) => PAD + (i * (W - PAD * 2)) / (sliced.length - 1);
  const y = (gap: number) => H / 2 - (gap / maxAbs) * (H / 2 - PAD);

  // 填充到零轴：负值（亏损）向下，正值向上——"剪刀差"的方向就是盈亏方向
  const zero = H / 2;
  const linePts = sliced.map((s, i) => `${x(i).toFixed(1)},${y(s.gap).toFixed(1)}`).join(" ");
  const areaPts = `${x(0).toFixed(1)},${zero} ${linePts} ${x(sliced.length - 1).toFixed(1)},${zero}`;
  const gapColor = last.gap >= 0 ? colors.up : colors.down;

  return (
    <div ref={ref} data-testid="cost-scissors" data-months={sliced.length}>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          justifyContent: "space-between",
          fontSize: 11.5,
          color: "var(--text-secondary)",
          marginBottom: 2,
        }}
      >
        <span>近 {sliced.length} 个月 · 生猪均价 − 行业成本</span>
        <span className="num" style={{ color: gapColor, fontWeight: 600 }} data-testid="cost-scissors-latest">
          {last.gap >= 0 ? "+" : ""}
          {last.gap.toFixed(2)} 元/kg · 头均 {(last.gap * PER_HEAD).toFixed(0)} 元
        </span>
      </div>
      <svg
        width="100%"
        height={H}
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`价格与成本剪刀差：最新 ${last.gap.toFixed(2)} 元每公斤`}
      >
        <line x1={PAD} y1={zero} x2={W - PAD} y2={zero} stroke={colors.border} strokeDasharray="3 3" />
        <polygon points={areaPts} fill={gapColor} opacity={0.14} />
        <polyline points={linePts} fill="none" stroke={gapColor} strokeWidth={1.6} strokeLinejoin="round" />
        <circle cx={x(sliced.length - 1)} cy={y(last.gap)} r={2.6} fill={gapColor} />
      </svg>
    </div>
  );
}
