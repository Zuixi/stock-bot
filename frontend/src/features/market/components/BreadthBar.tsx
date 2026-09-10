import "./BreadthBar.css";

export interface BreadthBarProps {
  up: number;
  down: number;
  /** 可读标签（无 aria-label 时兜底），如「煤炭 涨31 · 跌3」。 */
  label?: string;
}

/**
 * 行业/板块涨跌家数双向比例条：红色段为上涨占比、绿色段为下跌占比。
 *
 * 与 `DistributionBars` 同色约定（`--up`/`--down`），但形态更轻（4px 行内条），
 * 供行业行下方内嵌复用，不引入 ECharts。占比按 `up/(up+down)` 计算，平盘不计入。
 */
export function BreadthBar({ up, down, label }: BreadthBarProps) {
  const total = up + down;
  const upPct = total > 0 ? (up / total) * 100 : 0;
  const downPct = total > 0 ? (down / total) * 100 : 0;
  const text = label ?? `涨${up} · 跌${down}`;
  return (
    <span className="breadth-bar" data-testid="breadth-bar" title={text} aria-label={text}>
      <span className="breadth-bar__up" style={{ width: `${upPct}%` }} />
      <span className="breadth-bar__down" style={{ width: `${downPct}%` }} />
    </span>
  );
}
