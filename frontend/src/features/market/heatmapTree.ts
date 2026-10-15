import type { HeatmapStockItem } from "@/shared/api/heatmap";

/**
 * 热力图分组树构建（S1.5）——**纯函数**，与后端
 * `backend/app/services/heatmap_service.py:build_heatmap_groups` 同口径
 * （两边只锁「键与排序」，不锁实现）。
 *
 * 四个分组维度共用同一批 items：
 * - `all`      → 平铺（不组树，ECharts 直接吃叶子数组）；
 * - `sw_l1`    → 按 `sw_l1_code` 分组，组名由 `swNames` 解析，无码落「未分类」；
 * - `concept`  → 按 `concept_code` 分组，组名由 `conceptNames` 解析，无码落「未分类」；
 * - `watchlist`→ 调用方先用 `filterWatchlist` 过滤，再平铺（与 `all` 同形）。
 */

export type HeatmapGroupKey = "all" | "sw_l1" | "concept" | "watchlist";

export const HEATMAP_GROUPS: { key: HeatmapGroupKey; label: string }[] = [
  { key: "all", label: "全市场" },
  { key: "sw_l1", label: "申万一级" },
  { key: "concept", label: "概念板块" },
  { key: "watchlist", label: "自选" },
];

export type HeatmapAreaKey = "total_mv" | "amount";

export const HEATMAP_AREA_OPTIONS: { key: HeatmapAreaKey; label: string; unit: string }[] = [
  { key: "total_mv", label: "市值", unit: "万元" },
  { key: "amount", label: "成交额", unit: "千元" },
];

/** ECharts treemap 的叶子节点（颜色 = 涨跌幅，面积 = 面积维度字段）。 */
export interface HeatmapLeaf {
  /** 展示名（缺名回落代码）。 */
  name: string;
  /** 面积值；缺数行不进面积编码（ECharts 要求 value 可比），置 null 由调用方过滤。 */
  value: number | null;
  /** 涨跌幅（颜色维度，恒存在）。 */
  pct_chg: number;
  symbol: string;
}

/** 一片叶子是否可进画布：面积字段缺失的行如实剔除（不是补 0——那是伪造面积）。 */
export function toLeaf(item: HeatmapStockItem, area: HeatmapAreaKey): HeatmapLeaf | null {
  const raw = area === "total_mv" ? item.total_mv : item.amount;
  if (raw == null || !(raw > 0)) return null;
  return {
    name: item.name || item.symbol,
    value: raw,
    pct_chg: item.pct_chg,
    symbol: item.symbol,
  };
}

/** 按分组键把叶子组成 ECharts treemap 的两层树；`all`/`watchlist` 平铺。 */
export function buildTree(
  items: HeatmapStockItem[],
  group: HeatmapGroupKey,
  area: HeatmapAreaKey,
  swNames: Map<string, string>,
  conceptNames: Map<string, string>
): (HeatmapLeaf | { name: string; children: HeatmapLeaf[] })[] {
  const leavesOf = (rows: HeatmapStockItem[]): HeatmapLeaf[] =>
    rows.map((r) => toLeaf(r, area)).filter((l): l is HeatmapLeaf => l !== null);

  if (group === "all" || group === "watchlist") {
    return leavesOf(items);
  }

  const keyField = group === "sw_l1" ? "sw_l1_code" : "concept_code";
  const names = group === "sw_l1" ? swNames : conceptNames;
  const buckets = new Map<string, HeatmapStockItem[]>();
  for (const row of items) {
    const code = row[keyField];
    const key = code ?? "";
    const bucket = buckets.get(key);
    if (bucket) bucket.push(row);
    else buckets.set(key, [row]);
  }
  // 组序：按组名（缺名用码）字典序——与「行序」相比更稳：名录增删不打乱组顺序
  const orderedKeys = [...buckets.keys()].sort((a, b) => {
    const na = a === "" ? "" : (names.get(a) ?? a);
    const nb = b === "" ? "" : (names.get(b) ?? b);
    return na.localeCompare(nb, "zh-Hans-CN");
  });
  return orderedKeys.map((key) => {
    const label = key === "" ? "未分类" : (names.get(key) ?? key);
    return { name: label, children: leavesOf(buckets.get(key) ?? []) };
  });
}

/** 「自选」分组：用既有 watchlist 的 symbol 集合过滤（不建平行自选）。 */
export function filterWatchlist(
  items: HeatmapStockItem[],
  symbols: ReadonlySet<string>
): HeatmapStockItem[] {
  return items.filter((it) => symbols.has(it.symbol));
}
