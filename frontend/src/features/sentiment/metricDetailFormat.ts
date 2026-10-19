import type { SentimentMetricUnit } from "@/shared/api/limitUp";

/**
 * 指标详情页的展示换算（**只做量纲，不做统计**）。
 *
 * 后端契约里 `ratio` 是 0–1 的比值（0.4474 = 44.74%），`pct` 已经是百分数
 * （-1.638 = -1.64%）。这两类混用会渲染出 "0.45%" 这种差 100 倍的假数字
 * （历史 bug，见 `SentimentThermometer` 的 `pct()`）。
 */
export function metricUnitSuffix(unit: SentimentMetricUnit): string {
  switch (unit) {
    case "count":
      return "家";
    case "board":
      return "板";
    default:
      return "%";
  }
}

/** 值 → 展示串（缺失 `--`，绝不 0）。 */
export function formatMetricDisplay(
  value: number | null | undefined,
  unit: SentimentMetricUnit,
): string {
  if (value == null || Number.isNaN(value)) return "--";
  if (unit === "ratio") return `${(value * 100).toFixed(2)}%`;
  if (unit === "pct") return `${value.toFixed(2)}%`;
  return `${Math.round(value)}`;
}

/** 较前值 → 带符号串（比值差与百分数差都是"百分点"，计数差是整数）。 */
export function formatMetricDelta(
  delta: number | null | undefined,
  unit: SentimentMetricUnit,
): string {
  if (delta == null || Number.isNaN(delta)) return "--";
  const sign = delta > 0 ? "+" : delta < 0 ? "−" : "";
  const abs = Math.abs(delta);
  if (unit === "ratio") return `${sign}${(abs * 100).toFixed(2)}pp`;
  if (unit === "pct") return `${sign}${abs.toFixed(2)}pp`;
  return `${sign}${Math.round(abs)}`;
}

/**
 * 图表纵轴值：比值 ×100 与百分数同轴可比；计数/板原样。
 * 轴标签用 `metricAxisSuffix` 补单位，避免把 0.45 画成"0.45%"。
 */
export function metricAxisValue(value: number, unit: SentimentMetricUnit): number {
  return unit === "ratio" ? value * 100 : value;
}

export function metricAxisSuffix(unit: SentimentMetricUnit): string {
  return unit === "count" ? "家" : unit === "board" ? "板" : "%";
}

/** 直方图 x 轴等窄标签：只保留 1 位小数（10 个箱并排时 2 位会被截断成乱码）。 */
export function formatMetricCompact(
  value: number | null | undefined,
  unit: SentimentMetricUnit,
): string {
  if (value == null || Number.isNaN(value)) return "--";
  if (unit === "ratio") return `${(value * 100).toFixed(1)}%`;
  if (unit === "pct") return `${value.toFixed(1)}%`;
  return `${Math.round(value)}`;
}
