import type { KLinePoint } from "@/shared/types";
import { fmtAmount, fmtChangePct, fmtVolume, type MaKey } from "./klineMath";

/**
 * 图上方数据条（ribbon）的**纯取值层**：给定序列 + 索引，算出这一行该显示什么。
 *
 * 为什么单独成文件而不是写在 `KlineChart.tsx` 里：F10 的本质是"这一行随光标走"，
 * 取值规则（尤其是 `--` 语义）必须能脱离 React/ECharts 单测（`e2e/unit-kline-ribbon.spec.ts`）。
 * 组件只负责把结果画成 DOM —— **不进 ECharts option**：option 的 useMemo 一旦依赖
 * hover 索引，鼠标每动一像素就会重建 2400 点 option（`notMerge` 下必然掉帧）。
 *
 * 缺失 ≠ 0：没有前收（首根）→ `changePct` 为 `--`，不是 `0.00%`；
 * 没有 MA 值（窗口未成形 / 越界）→ `--`，不是 `0`。缺 `amount` 同样 `--`。
 */
export interface RibbonRow {
  date: string;
  open: string;
  high: string;
  low: string;
  close: string;
  changePct: string;
  volume: string;
  amount: string;
}

/** 索引必须是**整数且落在 [0, length)**；小数/NaN/负值/越界一律视为无效。 */
function isIndexInRange(length: number, index: number): boolean {
  return Number.isInteger(index) && index >= 0 && index < length;
}

/**
 * 某一根 K 线的 ribbon 行；索引越界或序列为空返回 `null`（调用方回落到最后一根）。
 *
 * 数值格式与 tooltip **逐字同源**：开/高/低/收 `toFixed(2)`、涨跌幅 `fmtChangePct`、
 * 成交量 `fmtVolume`（手/万手）、成交额 `fmtAmount`（亿）。同一根 K 线，ribbon 与
 * tooltip 不该出现两种写法。
 */
export function ribbonRow(points: KLinePoint[], index: number): RibbonRow | null {
  if (!isIndexInRange(points.length, index)) return null;
  const p = points[index];
  // 首根没有前收：涨跌幅是"不可知"，不是"0.00%"（tooltip 同一规则）
  const prevClose = index > 0 ? points[index - 1].close : undefined;
  return {
    date: p.date,
    open: p.open.toFixed(2),
    high: p.high.toFixed(2),
    low: p.low.toFixed(2),
    close: p.close.toFixed(2),
    changePct: fmtChangePct(p.close, prevClose),
    volume: fmtVolume(p.volume),
    amount: fmtAmount(p.amount),
  };
}

/**
 * 当前索引下**可见** MA 的取值（key = `MA5`/`MA10`/…，value 已格式化）。
 *
 * - 只返回 `visible` 里的键（不可见的 MA 行不显示数值，而不是显示 `--`）；
 * - 序列缺失 / 该点位为 `null`（窗口未成形，如第 3 根的 MA20）/ 索引越界 → `--`。
 *
 * 返回 `Record<string, string>` 而不是 `Record<MaKey, string>`：调用方按
 * `MA_DEFS` 遍历时键是联合类型，宽松索引避免每处断言（且便于测试任意键）。
 */
export function maValuesAt(
  maSeries: Partial<Record<MaKey, (number | null)[]>>,
  visible: MaKey[],
  index: number,
): Record<string, string> {
  const out: Record<string, string> = {};
  for (const key of visible) {
    const series = maSeries[key];
    const v = series && Number.isInteger(index) && index >= 0 ? series[index] : undefined;
    out[key] = v == null ? "--" : v.toFixed(2);
  }
  return out;
}
