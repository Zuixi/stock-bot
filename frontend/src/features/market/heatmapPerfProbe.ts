// S1.5 热力图性能探针（**仅 dev**，生产构建下为 no-op）。
//
// 唯一的职责：把**本应用自己的** ECharts 名称空间对象挂到 `window.__heatmapEchartsNS`，
// 供测量脚本给 `setOption` 包计时。另外记录 `buildTree` / 聚合等纯函数阶段耗时。
//
// 为什么只暴露名称空间、不在这里做包装：
//   测量脚本需要在 `init` 返回的**实例**上包 `setOption`（treemap 布局在 setOption
//   内同步完成，是 O(n) 大头，而调用**次数**才是判据关键）。而 `echarts.init = ...`
//   在**本模块内**会抛 `Cannot assign to property 'init' of [object Module]`——本模块
//   的 `echarts` 是 Vite 对 CJS 依赖做 interop 后的只读名称空间（实测：整页
//   ErrorBoundary 白屏）。测量脚本 import 到的又是**另一个**模块实例，在它上面改
//   同样无效。因此包装只能由测量脚本在拿到本模块暴露的名称空间后、于页面内完成：
//   那是一个普通可写对象（实测 `init` 赋值成功），且测量脚本的赋值动作发生在
//   echarts-for-react 首次 `init` 之前。
import * as echarts from "echarts";

/** 单个构建阶段的一条记录。 */
export interface HeatmapPhaseRecord {
  name: string;
  ms: number;
  leaves: number;
}

interface HeatmapProbeWindow {
  __heatmapEchartsNS?: typeof echarts;
  __heatmapPhases?: HeatmapPhaseRecord[];
}

const devWindow = (typeof window === "undefined" ? {} : window) as unknown as HeatmapProbeWindow;

if (import.meta.env.DEV) {
  devWindow.__heatmapEchartsNS = echarts;
  devWindow.__heatmapPhases = devWindow.__heatmapPhases ?? [];
}

/** 计时一个纯函数阶段并把结果写进 `__heatmapPhases`（dev 下；生产下直通）。 */
export function timeHeatmapPhase<T>(name: string, leaves: number, fn: () => T): T {
  if (!import.meta.env.DEV) return fn();
  const t0 = performance.now();
  const out = fn();
  const list = devWindow.__heatmapPhases;
  if (list) {
    list.push({ name, ms: performance.now() - t0, leaves });
    if (list.length > 16) list.splice(0, list.length - 16);
  }
  return out;
}

/** 聚合块在页面坐标系里的位置（测量脚本据此做**真实鼠标点击**）。 */
export interface AggregateTileRect {
  name: string;
  aggCount: number;
  /** 被并入叶子占本组的面积比例（0~1）。 */
  areaShare: number;
  /** 视口坐标（page.mouse.click 可直接用）。 */
  x: number;
  y: number;
  w: number;
  h: number;
}

interface TreemapLayout {
  x: number;
  y: number;
  width: number;
  height: number;
}

interface TreemapTreeNode {
  children?: TreemapTreeNode[];
  /** 该树节点对应的原始数据下标（映射回 `getOption().series[0].data`）。 */
  dataIndex?: number;
  getLayout?: () => TreemapLayout | undefined;
}

interface RawLeaf {
  name?: string;
  isAgg?: boolean;
  aggCount?: number;
  aggAreaShare?: number;
}

/**
 * 找到聚合块的屏幕矩形（**仅 dev，测量用**）。
 *
 * 三个实测坑（逐一试过才走通）：
 *   1. ECharts 的 treemap 没有坐标轴坐标系 —— `convertToPixel` 对它返回 `undefined`；
 *   2. `series.getViewRoot()` 返回的是**树节点**（没有 `traverse`），叶子节点的
 *      `viewChildren` 为空、也没有 `x/y/width/height`；布局只能调 `node.getLayout()`；
 *   3. 树节点的 `name` 属性是**个股名**（不是 option 里的 name），所以判定聚合块
 *      要用 `dataIndex` 回查 `getOption().series[0].data`。
 */
export function findAggregateTile(): AggregateTileRect | null {
  if (!import.meta.env.DEV) return null;
  const root = document.querySelector<HTMLElement>("div[_echarts_instance_]");
  if (!root) return null;
  const canvas = root.querySelector("canvas");
  if (!canvas) return null;
  const inst = echarts.getInstanceByDom(root) as unknown as {
    getModel?: () => {
      getSeriesByIndex?: (i: number) => { getData?: () => { tree?: { root?: TreemapTreeNode } } } | null;
    };
    getOption?: () => { series?: { data?: unknown }[] };
  } | null;
  const series = inst?.getModel?.()?.getSeriesByIndex?.(0);
  const rootNode = series?.getData?.()?.tree?.root;
  if (!rootNode) return null;

  const seriesData = inst?.getOption?.()?.series?.[0]?.data;
  const found = findAggNode(rootNode, Array.isArray(seriesData) ? (seriesData as RawLeaf[]) : []);
  if (!found) return null;
  const layout = found.getLayout?.();
  if (!layout || !layout.width || !layout.height) return null;
  const box = canvas.getBoundingClientRect();
  const rec = Array.isArray(seriesData) ? (seriesData as RawLeaf[])[found.dataIndex ?? -1] : undefined;
  return {
    name: rec?.name ?? "其他",
    aggCount: rec?.aggCount ?? 0,
    areaShare: rec?.aggAreaShare ?? 0,
    x: box.left + layout.x + layout.width / 2,
    y: box.top + layout.y + layout.height / 2,
    w: layout.width,
    h: layout.height,
  };
}

/** 在 treemap 树里找 `isAgg` 叶子（组节点也会递归，取面积最大者）。 */
function findAggNode(node: TreemapTreeNode, raw: RawLeaf[]): TreemapTreeNode | null {
  const idx = node.dataIndex ?? -1;
  if (idx >= 0 && raw[idx]?.isAgg) return node;
  let best: TreemapTreeNode | null = null;
  let bestArea = -1;
  for (const child of node.children ?? []) {
    const hit = findAggNode(child, raw);
    if (!hit) continue;
    const area = hit.getLayout?.()?.width ?? 0;
    if (area > bestArea) {
      best = hit;
      bestArea = area;
    }
  }
  return best;
}

if (import.meta.env.DEV) {
  (window as unknown as { __heatmapFindAggregateTile?: typeof findAggregateTile }).__heatmapFindAggregateTile =
    findAggregateTile;
}
