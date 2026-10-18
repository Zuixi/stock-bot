import { matchPath } from "react-router-dom";
import { getRoutePaths } from "./route-manifest";

/**
 * 路由模式键：把具体 pathname 归一到「路由清单里的模式」。
 *
 * 为什么需要它（2026-10-18 实测）：`App.tsx` 的 `RouteErrorBoundary` 原先用
 * `key={pathname}` 做"换页即重置错误态"。这对参数化路由是灾难——`/market/sentiment/zt_count`
 * 切到 `/market/sentiment/broken_rate` 时 pathname 变了，**整棵树（含 MainLayout）被卸载重挂**：
 * 骨架屏闪一下、图表重建、滚动位置丢失。用户看到的就是"点指标 tab 整页抖动"。
 *
 * 正确语义是「换**页面**才重置，换参数不重置」：用模式键（`/market/sentiment/:metric`）后，
 * 同一条路由内的参数变化保持组件与滚动位置；真正跳到另一条路由时仍会重置错误边界。
 *
 * 实现注意：本文件**不能**并入 `route-manifest.ts`——那份清单必须保持零依赖
 * （`frontend/scripts/check-routes.mjs` 由 node 直接 import），而这里要用 react-router 的 matchPath。
 */
const PATTERNS = [...new Set(getRoutePaths())]
  .filter((path) => path.length > 0)
  // 长的优先：`/market/sentiment/:metric` 必须先于 `/market` 命中
  .sort((a, b) => b.length - a.length);

export function routePatternKey(pathname: string): string {
  for (const pattern of PATTERNS) {
    if (matchPath({ path: pattern, end: true }, pathname)) return pattern;
  }
  // 清单外的路径（404、外部拼接）退回具体 pathname：宁可多重置一次，也不共用错误态
  return pathname;
}
