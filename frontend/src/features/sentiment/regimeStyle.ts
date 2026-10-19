import type { ThemePalette } from "@/app/theme";
import type { SentimentRegimeSegment, SentimentRegimeTone } from "@/shared/api/limitUp";

/**
 * 阶段色的唯一映射（Pulse 背景带 / 详情图背景带共用）。
 *
 * ECharts 不消费 CSS var，必须给具体色值；颜色语义与 CSS 的 `--sent-extreme-*` 对齐：
 * 冷=紫、热=棕、极热=红、中性=文字色、未知=边框色。
 */
export function regimeToneColor(tone: SentimentRegimeTone, c: ThemePalette): string {
  switch (tone) {
    case "cold":
      return c["sent-extreme-cold"];
    case "hot":
      return c["sent-extreme-hot"];
    case "extreme":
      return c.up;
    case "neutral":
      return c.textSecondary;
    default:
      return c.border;
  }
}

/** `#rrggbb` → `rgba(r,g,b,a)`；非 6 位色值原样返回（不猜）。 */
export function withAlpha(hex: string, a: number): string {
  const h = hex.replace("#", "");
  if (h.length !== 6) return hex;
  const n = Number.parseInt(h, 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${a})`;
}

/**
 * 把阶段区段裁到当前展示日期范围，生成 ECharts `markArea.data`。
 * 只画 tone 非 muted 的段；透明度默认 0.07（背景带不是主角）。
 */
export function regimeBandData(
  segments: SentimentRegimeSegment[],
  dates: string[],
  colors: ThemePalette,
  opacity = 0.07,
): Array<Array<Record<string, unknown>>> {
  if (!dates.length) return [];
  const first = dates[0];
  const last = dates[dates.length - 1];
  return segments
    .filter((s) => s.tone !== "muted" && s.to >= first && s.from <= last)
    .map((s) => [
      {
        xAxis: s.from < first ? first : s.from,
        itemStyle: { color: withAlpha(regimeToneColor(s.tone, colors), opacity) },
      },
      { xAxis: s.to > last ? last : s.to },
    ]);
}
