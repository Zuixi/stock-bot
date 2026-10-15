/**
 * 一级导航「策略引擎」的占位页（IA 文档 §5.2 no-404 契约）——模块真页面落地时替换本文件（并去掉 route-manifest.ts 的 isPlaceholder），路由形状不变 */
export default function StrategyPage() {
  return null; // 占位内容由路由清单注入的 PlaceholderNotice 渲染（见 route-elements.tsx）
}
