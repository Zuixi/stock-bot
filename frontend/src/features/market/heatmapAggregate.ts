import type { HeatmapLeaf } from "./heatmapTree";

/**
 * S1.5 热力图「小面积叶子聚合」——**纯函数**，与渲染解耦、可单测。
 *
 * ## 为什么需要
 *
 * spec §S1.5 要求「≥3000 标的的 treemap 首屏渲染不得阻塞主线程」。实测（见
 * `frontend/scripts/measure-heatmap.mjs` + task-9 报告）表明：把 5559 个叶子全量
 * 交给 ECharts 时，**单个**长任务就有 ~190ms，其 CPU 成本几乎全在 ECharts 内部的
 * treemap 布局与文本测量上（CDP profile：`Model.getShallow` 148ms、`Model.getModel`
 * 54ms、`measureText` 39ms），即**成本正比于叶子数**。分片着色、关闭入场动画都
 * 改变不了单片的绝对开销（两者实测均无改善，见报告）。
 *
 * 而画布只有约 1600×1000 CSS px：5559 个矩形平均 ~287px²，中位数以下的大量标的
 * 只有几像素——视觉上不承载信息（标签不显示、色块边界也分不出）。因此把视觉上
 * 不可分辨的叶子如实地聚合成「其他 N 只」块，比把它们全塞进布局更符合 spec 的
 * 「高信息密度」，也是唯一能把叶子数降下来的手段。
 *
 * ## 口径（诚实优先）
 *
 * - 两段式筛选：先按**全局**面积占比 `MIN_AREA_SHARE` 保留「画得出来」的块，
 *   再用全局矩形上界 `MAX_TOTAL_RECTS` 等比缩减各组的保留额度（见 `aggregateWithBudget`）；
 * - 其余并入该组的**一个**聚合块：
 *   - `value` = 被并入叶子的面积之和 → **面积守恒**（不伪造、不丢面积，测试里校验）；
 *   - `pct_chg` = 以面积为权的加权平均 —— 这是聚合块的「平均颜色」，
 *     tooltip 里显式说明该口径，不冒充某只个股的涨跌幅；
 *   - `aggCount` = 被并入的标的数（UI 如实显示「其他 N 只」）；
 * - 聚合是**可逆**的：聚合块可点击 → 该组丢弃聚合、展开全量叶子（drill-down）。
 */

/** 面积占比**下限**（占本次视图总面积）：低于它的叶子在任何情况下都不单独成块。
 *
 * 取 1e-4（0.01%）是「画布上还看得见吗」的下限：1600×1000 画布下 0.01% ≈ 160px²
 * （≈13×13px），比它还小的矩形连边框都糊在一起，不可能显示任何文字。
 * 这里刻意取得**很宽松**——真正决定渲染量的是下面的全局矩形上界，
 * 本阈值只负责兜住「绝对不可见」的极端小块，避免把可见标的误聚合掉。
 */
export const MIN_AREA_SHARE = 1e-4;

/**
 * 全局矩形上界：**决定渲染量的主杠杆**（实测得出）。
 *
 * 实测「最长任务 ≈ 矩形数的线性函数」：~55ms@约 400 块、~90ms@约 1500 块、
 * ~170ms@4453 块（1600×1000，同一批 5559 标的）。所以要让各视图都落进阈值，
 * 只能给矩形总数一个上界；上界之外的部分由「其他 N 只」聚合块如实承接
 * （面积守恒、颜色为面积加权均值、可点击下钻），不丢数据、不静默截断。
 *
 * 取值 900 是实测标定：首屏三个分组（全市场/申万一级/概念板块）的最长任务
 * 落在 79-93ms（多轮 5 次的观测区间），判据「最长任务 ≲100ms」达成且留有余量，
 * 同时仍单独画出约 900 个可见矩形（其余进聚合块）。
 * 早期取 1500 时分组视图偶发 103ms，故下调换余量。实测数字见 task-9 报告。
 */
export const MAX_TOTAL_RECTS = 900;

/**
 * 有效阈值（**仅 dev 可用 URL 覆盖**，生产恒为上面的常量）。
 *
 * 存在的意义是让 `scripts/measure-heatmap.mjs --min-share=… --max-total=…`
 * 能在不改代码的前提下实测不同阈值的代价/收益，把「取值依据」变成测量而不是猜。
 * 生产构建里 `import.meta.env.DEV` 为 false，直接短路成常量。
 */
function tune(defaultValue: number, param: string): number {
  if (!import.meta.env.DEV || typeof window === "undefined") return defaultValue;
  const raw = new URLSearchParams(window.location.search).get(param);
  const parsed = raw == null ? NaN : Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : defaultValue;
}

export function effectiveMinAreaShare(): number {
  return tune(MIN_AREA_SHARE, "min-share");
}

/** 全局矩形预算：0 = 关闭（默认）。>0 时按「矩形数 ∝ 面积」的比例分配各分组的保留额度。 */
export function effectiveMaxTotalRects(): number {
  if (!import.meta.env.DEV || typeof window === "undefined") return MAX_TOTAL_RECTS;
  const raw = new URLSearchParams(window.location.search).get("max-total");
  const parsed = raw == null ? NaN : Number(raw);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : MAX_TOTAL_RECTS;
}

/** 面积取值（缺数/非正数视为不可编码，不进面积，也不进权重）。 */
function areaOf(leaf: HeatmapLeaf): number {
  return leaf.value != null && leaf.value > 0 ? leaf.value : 0;
}

/** 聚合块叶子（ECharts 叶子形状 + 聚合元数据）。 */
export interface AggregateLeaf extends HeatmapLeaf {
  /** 聚合块标记。 */
  isAgg: true;
  /** 被并入聚合块的标的数（UI 如实显示）。 */
  aggCount: number;
  /** 被并入叶子占该组面积的比例（0~1）。 */
  aggAreaShare: number;
  /** 该聚合块所属分组（drill-down 用它定位展开哪个组）。 */
  aggKey: string;
}

/** 一个分组：`aggKey` 是 drill-down 的稳定标识（组名或 `all`）。 */
export interface HeatmapGroupInput {
  aggKey: string;
  children: HeatmapLeaf[];
}

/** 单个分组的聚合结果。 */
export interface HeatmapGroupOutput {
  aggKey: string;
  /** 聚合块被丢弃（drill-down 展开）时的分组键集合。 */
  children: (HeatmapLeaf | AggregateLeaf)[];
}

function isAggregateLeaf(node: HeatmapLeaf | AggregateLeaf): node is AggregateLeaf {
  return (node as AggregateLeaf).isAgg === true;
}
export { isAggregateLeaf };

/** 由被并入的叶子构造聚合块；`fold` 为空返回 null。 */
function makeAggregate(
  fold: HeatmapLeaf[],
  groupTotal: number,
  aggKey: string
): AggregateLeaf | null {
  if (!fold.length) return null;
  const area = fold.reduce((a, l) => a + areaOf(l), 0);
  const wsum = fold.reduce((a, l) => a + areaOf(l) * l.pct_chg, 0);
  return {
    // 全部缺面积时退化为等权均值（否则 0/0）
    pct_chg: area > 0 ? wsum / area : fold.reduce((a, l) => a + l.pct_chg, 0) / fold.length,
    value: area > 0 ? area : null,
    name: `其他 ${fold.length} 只`,
    symbol: "",
    isAgg: true,
    aggCount: fold.length,
    aggAreaShare: groupTotal > 0 ? area / groupTotal : 0,
    aggKey,
  };
}

/**
 * 聚合入口。
 *
 * 阈值口径是**全局**的：一个叶子保留的条件是「其面积 ≥ 本次视图**总面积** ×
 * `MIN_AREA_SHARE`」，而不是「其面积 ≥ 本组面积 × 阈值」。这一点是实测逼出来的：
 * 按组分阈值时，小组里的碎块（占本组比例不小、但绝对面积只有几像素）会全部留下，
 * 申万一级 32 组合计仍有 4600+ 个矩形，切分组的长任务依旧是 170ms；改成全局口径后
 * 同一份数据只留 ~1100 个矩形，视觉语义还是「只去掉画不出来的块」。
 *
 * @param groups        分组（平铺视图传一个 `aggKey: "all"` 的组）
 * @param expandedKeys  drill-down 已展开的分组键 → 这些组不做聚合（全量渲染）
 */
export function aggregateGroups(
  groups: HeatmapGroupInput[],
  expandedKeys: ReadonlySet<string> = new Set(),
  /**
   * 覆盖矩形预算（Phase 3 §4.3 / §0.1 B2）。
   *
   * 为什么需要按**画布宽度**覆盖：`MAX_TOTAL_RECTS = 900` 是按宽屏画布定的
   * （`/market/heatmap` 在 1440 下画布 ~1150px）。同一份 900 个矩形塞进 390 视口的
   * **308px** 画布时，平均每块只有 ~10×5px —— 此时 `labelLayout: { hideOverlap: true }`
   * 只能保证"文字不互相压住"，保证不了"有地方放字"：实测窄屏下大部分块**一个标签都放不下**，
   * 而少数放得下的块又散落在碎块海里，热力图整体不可读（截图见
   * `evidence/phase3-heatmap-narrow-element.png`）。
   *
   * 传 `undefined` 时行为与整改前**完全一致**（仍用 `effectiveMaxTotalRects()`），
   * 故 `scripts/check-heatmap-aggregate.mjs` 与任何既有调用点不受影响。
   */
  budgetOverride?: AggregateBudget
): HeatmapGroupOutput[] {
  return aggregateWithBudget(
    groups,
    expandedKeys,
    budgetOverride ?? {
      minShare: effectiveMinAreaShare(),
      maxTotal: effectiveMaxTotalRects(),
    }
  );
}

/**
 * 按画布**实测宽度**算矩形预算（Phase 3 §4.3，B2）。
 *
 * 口径：每约 1.8px 画布宽度允许一个矩形（宽屏 1150px ÷ 1.8 ≈ 639，仍低于既有
 * `MAX_TOTAL_RECTS` 900 → 宽屏行为不变，只有窄屏收敛），下限 90（再少就只剩几十个块，
 * 热力图失去"全市场概览"的意义）。
 *
 * ⚠️ 这是**可读性**参数，不是性能参数：性能上界仍是 `MAX_TOTAL_RECTS`
 * （`heatmapAggregate` 的注释与 `scripts/measure-heatmap.mjs` 的证据都指向它）。
 */
export function rectBudgetForWidth(width: number): AggregateBudget {
  return {
    minShare: effectiveMinAreaShare(),
    maxTotal: Math.max(90, Math.min(effectiveMaxTotalRects(), Math.round(width / 1.8))),
  };
}

/** 聚合的可注入参数（测试直接传，避免依赖 window/URL）。 */
export interface AggregateBudget {
  /** 全局面积占比阈值（占本次视图总面积）。 */
  minShare: number;
  /** 全局矩形上界；<=0 表示不设上界。 */
  maxTotal: number;
}

/** 单个分组的「保留判定」：返回保留集合（含按上限截断）。 */
function selectKept(
  children: HeatmapLeaf[],
  totalAreaOfView: number,
  minShare: number,
  limit: number
): Set<HeatmapLeaf> {
  const sorted = [...children].sort((a, b) => areaOf(b) - areaOf(a));
  const keep = new Set<HeatmapLeaf>();
  for (let r = 0; r < sorted.length && keep.size < limit; r++) {
    const shareOfView = totalAreaOfView > 0 ? areaOf(sorted[r]) / totalAreaOfView : 0;
    if (shareOfView >= minShare) keep.add(sorted[r]);
  }
  return keep;
}

/**
 * 聚合实现（纯函数、参数显式）。
 *
 * 两段式：
 *  1. **面积可读性**：保留「面积 ≥ 本次视图总面积 × minShare」的叶子，其余聚合。
 *     这一步决定「哪些块小到画不出来」，与分组无关。
 *  2. **矩形预算**：若第 1 步之后矩形总数超过 `maxTotal`，按比例等比缩减每个分组的
 *     保留额度（比例一致 → 各分组保留的优先级口径不变），直到总数落进预算。
 *
 * 为什么需要第 2 步：分组视图下每个组分都有自己的一批「可读」块，32 个申万一级
 * 组合计仍有 4000+ 个矩形（实测长任务 150-200ms）。分组是用户主动切换的视图，
 * 但既然矩形总数是唯一可控杠杆，就用一个全局预算把它按住。
 */
export function aggregateWithBudget(
  groups: HeatmapGroupInput[],
  expandedKeys: ReadonlySet<string>,
  budget: AggregateBudget
): HeatmapGroupOutput[] {
  const { minShare, maxTotal } = budget;
  const areas = groups.map((g) => g.children.reduce((a, l) => a + areaOf(l), 0));
  const totalArea = areas.reduce((a, b) => a + b, 0);

  // 第一段：按面积阈值保留（上限先用该组叶子数，即不截断）
  const keptSets = groups.map((g, i) =>
    expandedKeys.has(g.aggKey)
      ? null
      : selectKept(g.children, totalArea, minShare, g.children.length)
  );

  // 第二段：全局预算 → 等比缩减每组的保留额度
  const rawTotal = keptSets.reduce((a, s) => a + (s ? s.size : 0), 0);
  const limits: (number | null)[] = groups.map((g, i) =>
    expandedKeys.has(g.aggKey) ? null : (keptSets[i] as Set<HeatmapLeaf>).size
  );
  if (maxTotal > 0 && rawTotal > maxTotal) {
    const ratio = maxTotal / rawTotal;
    for (let i = 0; i < groups.length; i++) {
      if (limits[i] == null) continue;
      // 每组至少留 12 块，避免小组被压成空壳（剩余面积进聚合块，语义不变）
      limits[i] = Math.max(24, Math.floor((limits[i] as number) * ratio));
    }
    for (let i = 0; i < groups.length; i++) {
      if (expandedKeys.has(groups[i].aggKey)) continue;
      keptSets[i] = selectKept(groups[i].children, totalArea, minShare, limits[i] as number);
    }
  }

  return groups.map((group, i) => {
    if (expandedKeys.has(group.aggKey)) {
      return { aggKey: group.aggKey, children: group.children };
    }
    const keep = keptSets[i] as Set<HeatmapLeaf>;
    const kept: HeatmapLeaf[] = [];
    const fold: HeatmapLeaf[] = [];
    for (const leaf of group.children) {
      if (areaOf(leaf) > 0 && keep.has(leaf)) kept.push(leaf);
      else fold.push(leaf);
    }
    const agg = makeAggregate(fold, areas[i], group.aggKey);
    return { aggKey: group.aggKey, children: agg ? [...kept, agg] : kept };
  });
}

/** 面积合计（测试用：聚合前后必须相等）。 */
export function areaTotal(nodes: (HeatmapLeaf | AggregateLeaf)[]): number {
  return nodes.reduce((a, n) => a + areaOf(n), 0);
}

/** 叶子数（不含组节点；聚合块计 1）。 */
export function rectCount(outputs: HeatmapGroupOutput[]): number {
  return outputs.reduce((a, g) => a + g.children.length, 0);
}
