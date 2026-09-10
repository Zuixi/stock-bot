const DASH = "—";

export const fmtYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${(v / 1e8).toFixed(digits)}亿`;

export const fmtSignedYi = (v: number | null | undefined, digits = 2): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e8).toFixed(digits)}亿`;

export const fmtWanYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿`;

export const fmtWanGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${v.toFixed(0)}万股`;

export const fmtYiGu = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿股`;

export const fmtNorthYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e4).toFixed(2)}亿`;

/**
 * 相对时间（Intl.RelativeTimeFormat("zh")）：如「2小时前」「3天前」。
 * 解析失败返回 `--`，绝不回退成「刚刚」（缺失语义与零值不同）。
 */
export function fmtRelativeTime(iso: string, now: number = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return DASH;
  const diffSec = Math.round((t - now) / 1000);
  const abs = Math.abs(diffSec);
  const rtf = new Intl.RelativeTimeFormat("zh", { numeric: "auto" });
  if (abs < 60) return rtf.format(diffSec, "second");
  if (abs < 3600) return rtf.format(Math.round(diffSec / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diffSec / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diffSec / 86400), "day");
  if (abs < 86400 * 365) return rtf.format(Math.round(diffSec / (86400 * 30)), "month");
  return rtf.format(Math.round(diffSec / (86400 * 365)), "year");
}
