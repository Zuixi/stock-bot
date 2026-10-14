import { useEffect, useMemo, useRef, useState } from "react";
import { EChart } from "@/shared/ui/EChart";
import { useElementWidth } from "@/shared/ui";
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
  rectBudgetForWidth,
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
 * - 有效手段是**降低叶子数**：先按全局面积占比阈值 `MIN_AREA_SHARE` 筛掉「画不出来」的
 *   叶子，再用全局矩形预算 `MAX_TOTAL_RECTS` 把总数压住（实测唯一管用的杠杆），
 *   被剔除的叶子并成一个「其他 N 只」块（面积守恒、颜色为面积加权均值、可点击下钻）。
 *   视觉上只去掉「本来就不显示标签、边界也分不出」的几像素矩形。
 *
 * 故：**一次** `setOption` 画完全部颜色（不做逐帧重排），叶子数由
 * `heatmapAggregate` 控制（口径与阈值依据见该文件注释）。
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

/**
 * label 防重叠策略（Phase 3 §4.3，B2：treemap 必须保留，只解决标签重叠）。
 *
 * ⚠️ **实测更正计划 §4.3 的措辞**：treemap 没有"面积阈值"式配置，但**它确实调用
 * `labelLayout` 回调**（已用运行时探针核实：回调收到 `{ dataIndex, text, rect, labelRect }`，
 * `rect` 就是该方块的**实测几何**）。防重叠因此有两层，缺一不可：
 *
 * 1. `labelLayout: () => ({ hideOverlap: true })` —— ECharts 的 `LabelManager` 对勾选了
 *    该选项的标签做**全局矩形互斥**（`hideOverlap()` 把被压住的标签 `ignore = true`，
 *    **悬停时仍会显示**，见 `labelLayoutHelper.js` 的 `hideEl`）。这是"文字不互相压住"。
 * 2. **按画布宽度收矩形预算**（`rectBudgetForWidth`，见 `heatmapAggregate.ts`）——
 *    `hideOverlap` 只保证"不重叠"，不保证"有地方放字"：900 个矩形塞进 308px 画布时
 *    每块平均 ~10×5px，结果是"极少数块有标签、其余全空"，热力图整体不可读。
 *    把预算收到 ~宽度/1.8（窄屏 ~170 块）后每块才有可读面积。
 *    ⚠️ 这条**不是**性能优化，是**可读性**手段：性能上界仍是 `MAX_TOTAL_RECTS`。
 *
 * 标签文本本身**不再按字数/尺寸裁剪**：把名称写进标签，放不下时由 treemap 自己的
 * `truncateMinChar = 2` + `lineOverflow = 'truncate'` 截断（这在窄块里就是"读不全"），
 * 而"整块太小、放不下任何文字"的情形交给第 1 层的 `hideOverlap`。
 * 早期版本按 `name.length > 6` 丢名称——它会把**占比最大的聚合块**（`其他 5389 只`）
 * 的名字也一并丢掉，于是画面上最大的一块反而没有身份标识（实测截图）。
 */
function labelFormatter(params: unknown): string {
  const d = (params as { data?: HeatmapLeaf }).data;
  if (!d || d.pct_chg == null) return "";
  const sign = d.pct_chg > 0 ? "+" : "";
  const pctText = `${sign}${d.pct_chg.toFixed(1)}%`;
  const name = d.name ?? "";
  return name.length > 0 ? `{n|${name}}\n{p|${pctText}}` : `{p|${pctText}}`;
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
  /**
   * 画布**实测**宽度 → 矩形预算（Phase 3 §4.3，B2 的"按容器宽度"落点之一）。
   *
   * `width === 0` 是首帧（`ResizeObserver` 回调前）：此时**不覆盖**预算，
   * 沿用 `MAX_TOTAL_RECTS` 的既有行为——回落成 900 是"宽屏假设"，
   * 与整改前一致；一旦量到真实宽度（窄屏 308px）就收敛到约 170 个矩形。
   * 这样"首帧先按大预算画、下一帧收敛"只在极窄屏出现一次重排，且不会画出假的密度。
   */
  const { ref: chartRef, width: chartWidth } = useElementWidth<HTMLDivElement>();

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
    () =>
      timeHeatmapPhase("aggregate", items.length, () =>
        aggregateGroups(
          toGroupInputs(tree, group),
          expanded,
          chartWidth > 0 ? rectBudgetForWidth(chartWidth) : undefined
        )
      ),
    [tree, group, expanded, items.length, chartWidth]
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
          /**
           * 标签防重叠（Phase 3 §4.3）。返回 `hideOverlap` 后由 ECharts 的
           * `LabelManager` 做矩形互斥：被压住的标签 `ignore = true`（**悬停仍显示**），
           * 于是窄屏 308px 画布里的 900 个矩形不再糊成一片。
           *
           * 保留 `rect` 的读取形式：回调参数里 `rect` 是该方块的实测几何——
           * 将来若要按面积做更细的分级（例如"宽 < 30px 连涨跌幅也不画"），
           * 应该在这里判，**不要**回到 `labelFormatter` 里找不存在的 `params.data.width`。
           */
          labelLayout: () => ({ hideOverlap: true }),
          data,
        },
      ],
    };
  }, [grouped, colors, area]);

  return (
    // 外层 div 只为量宽度（`useElementWidth`）：`EChart` 的根是 zrender 的 canvas 容器，
    // 我们没有它的 ref。宽度本身**不改变图的内容语义**，只决定矩形预算（见上）。
    <div ref={chartRef} style={{ width: "100%" }}>
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
    </div>
  );
}
