import { useEffect, useMemo, useRef, useState } from "react";
import { EChart } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import { hexLerp } from "./components/DistributionChart";
import {
  buildTree,
  HEATMAP_AREA_OPTIONS,
  type HeatmapAreaKey,
  type HeatmapGroupKey,
  type HeatmapLeaf,
} from "./heatmapTree";
import {
  aggregateGroups,
  isAggregateLeaf,
  type AggregateLeaf,
  type HeatmapGroupInput,
} from "./heatmapAggregate";
import type { HeatmapStockItem } from "@/shared/api/heatmap";
import type { ThemePalette } from "@/app/theme";
import { timeHeatmapPhase } from "./heatmapPerfProbe";

/**
 * S1.5 市场/个股热力图（treemap，对标 iFinD）。
 *
 * 面积 = 市值或成交额（`area` 切换）；颜色 = 涨跌幅（红涨绿跌，语义固定）。
 *
 * ## 性能契约（spec §S1.5：≥3000 标的首屏渲染不得阻塞主线程）
 *
 * 实测结论与做法（数字见 `scripts/measure-heatmap.mjs` 与 task-9 报告）：
 *
 * - 全量 5559 个叶子交给 ECharts 时**单个**长任务就有 ~190ms，成本几乎全在
 *   ECharts 内部的 treemap 布局与文本测量（CDP profile：`Model.getShallow`
 *   148ms、`Model.getModel` 54ms、`measureText` 39ms）——**正比于叶子数**。
 * - 因此「时间分片逐帧着色」是**错误**的方向：ECharts 的 `option` 每次变化都会
 *   整体重排（`EChart` 固定 `notMerge`），分片只会把一次 190ms 的重排复制成
 *   N 次（实测 7 次 ≈ 每片 150-200ms）。关闭入场动画同样无改善（实测）。
 * - 有效手段是**降低叶子数**：把组内面积占比低于 `MIN_AREA_SHARE` 的叶子聚合为
 *   一个「其他 N 只」块（面积守恒、颜色为面积加权均值、可点击下钻），
 *   视觉上只去掉「本来就不显示标签、边界也分不出」的几像素矩形。
 *
 * 故：**一次** `setOption` 画完全部颜色（不做逐帧重排），叶子数由
 * `heatmapAggregate` 控制。
 */

/** 涨跌幅 → 色阶：0% 中性面板色，±5% 全红/全绿（与 SectorHeatmap 同梯度口径）。 */
function heatColor(pct: number, c: ThemePalette): string {
  const t = Math.min(1, Math.abs(pct) / 5);
  return pct >= 0 ? hexLerp(c.bgPanel, c.up, t) : hexLerp(c.bgPanel, c.down, t);
}

interface Props {
  items: HeatmapStockItem[];
  group: HeatmapGroupKey;
  area: HeatmapAreaKey;
  swNames: Map<string, string>;
  conceptNames: Map<string, string>;
  height?: number;
  onSymbolClick?: (symbol: string) => void;
  /** 聚合口径变化时上报（供页面如实标注「N 只已聚合」）。 */
  onAggregated?: (info: { rendered: number; aggregated: number; groups: number }) => void;
}

/** label 富文本：块够大显示 名称+涨跌幅，小只显示涨跌幅，再小不显示（密度优先）。 */
function labelFormatter(params: unknown): string {
  const d = (params as { data?: HeatmapLeaf }).data;
  if (!d || d.pct_chg == null) return "";
  const sign = d.pct_chg > 0 ? "+" : "";
  const pctText = `${sign}${d.pct_chg.toFixed(1)}%`;
  const name = d.name ?? "";
  return name.length > 4 ? `{p|${pctText}}` : `{n|${name}}\n{p|${pctText}}`;
}

/** 分组树 → 聚合输入（平铺视图包成一个 `aggKey: "all"` 的组）。 */
function toGroupInputs(
  tree: (HeatmapLeaf | { name: string; children: HeatmapLeaf[] })[],
  group: HeatmapGroupKey
): HeatmapGroupInput[] {
  if (group === "all" || group === "watchlist") {
    return [{ aggKey: "all", children: tree as HeatmapLeaf[] }];
  }
  return (tree as { name: string; children: HeatmapLeaf[] }[]).map((g) => ({
    aggKey: `group:${g.name}`,
    children: g.children,
  }));
}

export function HeatmapTreemap({
  items,
  group,
  area,
  swNames,
  conceptNames,
  height = 560,
  onSymbolClick,
  onAggregated,
}: Props) {
  const { colors } = useTheme();
  /** drill-down：已展开（不聚合）的分组键。 */
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => new Set());
  const onAggregatedRef = useRef(onAggregated);
  onAggregatedRef.current = onAggregated;

  // 树结构：组 + 面积维度变化才重算（着色不再驱动重算）
  const tree = useMemo(
    () =>
      timeHeatmapPhase("buildTree", items.length, () =>
        buildTree(items, group, area, swNames, conceptNames)
      ),
    [items, group, area, swNames, conceptNames]
  );

  // 分组变化时清空 drill-down（旧键在新树里没有意义）
  useEffect(() => {
    setExpanded(new Set());
  }, [group]);

  const grouped = useMemo(
    () => timeHeatmapPhase("aggregate", items.length, () => aggregateGroups(toGroupInputs(tree, group), expanded)),
    [tree, group, expanded, items.length]
  );

  const rendered = useMemo(() => grouped.reduce((n, g) => n + g.children.length, 0), [grouped]);
  const aggregated = useMemo(
    () =>
      grouped.reduce(
        (n, g) => n + g.children.reduce((m, c) => m + (isAggregateLeaf(c) ? c.aggCount : 0), 0),
        0
      ),
    [grouped]
  );
  const aggGroups = useMemo(
    () => grouped.reduce((n, g) => n + g.children.filter(isAggregateLeaf).length, 0),
    [grouped]
  );

  useEffect(() => {
    onAggregatedRef.current?.({ rendered, aggregated, groups: aggGroups });
  }, [rendered, aggregated, aggGroups]);

  const option = useMemo(() => {
    const paint = (leaf: HeatmapLeaf | AggregateLeaf) => {
      const agg = isAggregateLeaf(leaf);
      return {
        ...leaf,
        itemStyle: {
          color: heatColor(leaf.pct_chg, colors),
          borderColor: colors.bgPage,
          borderWidth: 1,
          // 聚合块：虚线边框，视觉上与个股块区分（点击可下钻）
          ...(agg ? { borderColor: colors.textSecondary, borderWidth: 1.2, borderType: "dashed" } : {}),
        },
      };
    };
    const isFlat = grouped.length === 1 && grouped[0].aggKey === "all";
    const data = isFlat
      ? grouped[0].children.map(paint)
      : grouped.map((g) => ({
          name: g.aggKey.replace(/^group:/, ""),
          children: g.children.map(paint),
          label: { fontSize: 13, fontWeight: 600 as const, color: colors.textPrimary },
          upperLabel: { show: true, height: 22, color: colors.textPrimary },
          itemStyle: { color: colors.bgPanel, borderColor: colors.bgPage, borderWidth: 2 },
        }));
    return {
      // 大节点数 treemap 的入场动画是逐帧重绘（实测关掉可省一次重绘），
      // 且首屏判据关注的是「画出来」，动画只会推迟可交互时刻。
      animation: false,
      tooltip: {
        formatter: (params: unknown) => {
          const d = (params as { data?: HeatmapLeaf | AggregateLeaf }).data;
          if (!d || d.pct_chg == null) return "";
          const sign = d.pct_chg > 0 ? "+" : "";
          const areaOpt = HEATMAP_AREA_OPTIONS.find((o) => o.key === area);
          const val =
            d.value == null
              ? "—"
              : d.value >= 1e8
                ? `${(d.value / 1e8).toFixed(1)}亿${areaOpt?.unit ?? ""}`
                : `${(d.value / 1e4).toFixed(1)}万${areaOpt?.unit ?? ""}`;
          if (isAggregateLeaf(d)) {
            return (
              `<b>${d.name}</b><br/>` +
              `合计面积占本组 ${(d.aggAreaShare * 100).toFixed(1)}%<br/>` +
              `涨跌幅（按面积加权平均）: ${sign}${d.pct_chg.toFixed(2)}%<br/>` +
              `点击可展开这 ${d.aggCount} 只`
            );
          }
          return `<b>${d.name}</b> (${d.symbol})<br/>涨跌: ${sign}${d.pct_chg.toFixed(2)}%<br/>${areaOpt?.label ?? "面积"}: ${val}`;
        },
      },
      // 分组组头条（upperLabel）的底色必须显式给：ECharts 默认继承 itemStyle.color，
      // 组节点是中性面板色时组头会与块面融为一体（实测组名"隐形"）。
      series: [
        {
          type: "treemap" as const,
          roam: false,
          nodeClick: false,
          breadcrumb: { show: false },
          width: "100%",
          height: "100%",
          upperLabel: {
            show: !isFlat,
            height: 22,
            color: colors.textPrimary,
            // 组头底色：给中性面板色（组节点自己的 itemStyle.color 是同一个值，
            // ECharts 组头默认继承它 → 组头"隐形"；显式同色即正常渲染组名文字）。
            backgroundColor: colors.bgPanel,
          },
          itemStyle: { borderColor: colors.bgPage, borderWidth: 1, gapWidth: 1 },
          label: {
            show: true,
            formatter: labelFormatter,
            rich: {
              n: { fontSize: 11, lineHeight: 14, color: colors.textPrimary },
              p: { fontSize: 10, lineHeight: 12, color: colors.textSecondary },
            },
          },
          data,
        },
      ],
    };
  }, [grouped, colors, area]);

  return (
    <EChart
      option={option}
      height={height}
      onEvents={{
        click: (params: unknown) => {
          const d = (params as { data?: HeatmapLeaf | AggregateLeaf }).data;
          if (!d) return;
          if (isAggregateLeaf(d)) {
            // drill-down：丢弃该组的聚合缓存 → 全量渲染该组（UI 已提示可点击）
            setExpanded((prev) => {
              const next = new Set(prev);
              next.add(d.aggKey);
              return next;
            });
            return;
          }
          if (d.symbol) onSymbolClick?.(d.symbol);
        },
      }}
    />
  );
}
