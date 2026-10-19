import { QuoteBar, type QuoteBarProps } from "./QuoteBar";

export type StockHeaderProps = QuoteBarProps;

/**
 * 兼容层：**报价条本体在 `QuoteBar.tsx`**（四行：身份 / 价格 / 八项事实 / 区间与分位）。
 *
 * 为什么保留这个名字：页面与既有文档/e2e 都以 `StockHeader` 指代"详情页顶部那条读数区"，
 * 改名会带来无收益的连锁改动。实现已拆到 `QuoteBar` / `QuoteMetricRow` / `PositionStrip` /
 * `RangeRibbon` 四个文件，本文件只做名字映射 —— 不要再把逻辑写回来。
 */
export function StockHeader(props: QuoteBarProps) {
  return <QuoteBar {...props} />;
}
