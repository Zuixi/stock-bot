import type { StockRangeStats } from "../hooks/useStockRangeStats";

/**
 * 52 周区间条（设计稿 §1「本版唯一的大胆处」）：用**位置**代替三个数字回答"现在贵不贵"。
 *
 * ```
 * 距高 -18.3%   ├──┬──●──┬──┤   距低 +12.4%
 *               52w低  25 50 75  52w高
 * ```
 *
 * - 无动效、无渐变；轨道/刻线走 `--border`，游标走 `--accent`（**不是**涨跌色：
 *   距离不是涨跌，用中性色 + 品牌色）。
 * - `<768` 由 CSS 隐藏（见 QuoteBar.css），信息回落到两段百分比文字。
 * - `aria-label` 同时给出区间位置与两段距离，读屏用户拿到与视觉等价的结论。
 */
export function RangeRibbon({ stats }: { stats: StockRangeStats }) {
  const position = Math.round(stats.positionPct);
  const label =
    `现价位于 52 周区间 ${position}%，` +
    `距高点 ${Math.abs(stats.distanceToHigh).toFixed(1)}%，` +
    `距低点 ${Math.abs(stats.distanceToLow).toFixed(1)}%`;

  return (
    <div className="range-ribbon" role="img" aria-label={label} data-testid="range-ribbon">
      <div className="range-ribbon__track" />
      {[25, 50, 75].map((tick) => (
        <div key={tick} className="range-ribbon__tick" style={{ left: `${tick}%` }} />
      ))}
      <div
        className="range-ribbon__cursor"
        style={{ left: `calc(${stats.positionPct}% - 1.5px)` }}
      />
    </div>
  );
}
