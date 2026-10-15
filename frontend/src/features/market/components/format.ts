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

/** 净买入（万元）→ 亿元：**带正负号**（净买入有方向）。 */
export const fmtNorthYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${v > 0 ? "+" : ""}${(v / 1e4).toFixed(2)}亿`;

/**
 * 成交额（万元）→ 亿元：**不带正负号**。
 *
 * 成交额没有方向，标 `+`/`−` 会诱导读者把它当成净流入（这正是 2024-08-19 之后
 * `north_money` 被错标的那类误读）。缺值仍是 `—`，绝不回落 0.00。
 */
export const fmtTurnoverYi = (v: number | null | undefined): string =>
  v == null ? DASH : `${(v / 1e4).toFixed(2)}亿`;

/**
 * TuShare 原生 `amount`（千元）→ 元 → 万亿/亿，拆成 `DataRow` 的 value/unit 两段。
 * 缺失返回 `{}`，由消费方（如 `DataRow valuePlaceholder="--"`）决定占位符；
 * 绝不回退 0.00。
 *
 * ⚠️ 单位是**最简写法**「亿」/「万亿」，不是「亿元」：本函数唯一的两个消费方
 * （`RankingMatrix`、`SectorFlow`）都把数值放进固定宽度的槽位，而 P1-2 的三元组价格槽
 * 宽 72px 是硬预算（390 视口下必须给标识列让路）。实测「1000.00 亿元」需要 ~85px，
 * 会溢出到左边的名称上；「1000.00 亿」只要 ~65px，同槽可容纳 4 位数的成交额。
 * 口径不变（仍是人民币亿元），只是省掉重复的「元」字。
 */
export function fmtAmountParts(
  amountQian: number | null | undefined,
): { value?: string; unit?: string } {
  if (amountQian == null) return {};
  const yuan = amountQian * 1e3;
  if (Math.abs(yuan) >= 1e12) return { value: (yuan / 1e12).toFixed(2), unit: "万亿" };
  return { value: (yuan / 1e8).toFixed(2), unit: "亿" };
}

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

/**
 * `as_of`（YYYY-MM-DD）→「9月9日」样式（榜单与板块的「数据截至」共用）。
 * 共享实现见 @/shared/ui/date，此处仅转发以保留既有导出名（RankingMatrix 等零改动）。
 */
export { formatCnDate } from "@/shared/ui/date";
