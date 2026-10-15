/**
 * 指标值的展示格式（**只做单位后缀与小数位，不做任何语义换算**）。
 *
 * 三条约定：
 *  1. `null` 一律 `--`（缺数不是 0）；
 *  2. 单位来自后端 `unit` 字段，前端不猜；
 *  3. 计数类（家 / 板）取整，比率类（% / 倍 / 亿元 / 亿份 / 万股 / 分）保留两位。
 */
import type { SentimentPercentileField } from "@/shared/api/limitUp";

const UNIT_PRECISION: Record<string, number> = {
  "%": 2,
  倍: 2,
  分: 2,
  亿元: 2,
  亿份: 2,
  万股: 2,
  家: 0,
  板: 0,
};

/** 数值 + 单位（单位为空串时只给数值）。 */
export function formatMetricValue(value: number | null | undefined, unit: string | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "--";
  const suffix = unit ?? "";
  const precision = UNIT_PRECISION[suffix] ?? 2;
  return `${value.toFixed(precision)}${suffix}`;
}

/**
 * 分位梯里每个字段的量纲：`zt_count` 是家数（整数），`broken_rate` / `promo_1to2`
 * 是后端契约里的**比值**（0.1585 = 15.85%），必须 ×100 再渲染
 * —— 直接 `toFixed` 会显示成 0.16%（历史 bug，见 SentimentThermometer 的同一处理）。
 */
export const PERCENTILE_FIELD_IS_RATIO: Record<SentimentPercentileField, boolean> = {
  zt_count: false,
  broken_rate: true,
  promo_1to2: true,
};

/** 分位梯上的一个分位点（nearest-rank 原值，不插值）。 */
export function formatQuantile(
  value: number | null | undefined,
  field: SentimentPercentileField,
): string {
  if (value == null) return "--";
  return PERCENTILE_FIELD_IS_RATIO[field] ? `${(value * 100).toFixed(2)}%` : `${Math.round(value)}`;
}

/** 后端 `freq` 的展示文案；未知值原样透出（便于发现契约漂移，不静默美化）。 */
export const FREQ_LABEL: Record<string, string> = {
  daily: "日频",
  weekly: "周度",
  monthly: "月频",
  quarterly: "季度",
  event: "事件驱动",
};
