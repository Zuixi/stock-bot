/**
 * 日期口径的**单一入口**（计划 §3.9 / 门禁 §8.2）。
 *
 * 为什么必须只有这一处：2026-10-13 全站评审实测同一产品里并存**四种日期写法**
 * （`10月9日` / `2026-10-12T15:00:00Z` / `1 天前` / `4 天前`），其中机器串（ISO）
 * 直接上屏，人读不出"这是收盘还是盘中"。静态门禁 `scripts/check-no-raw-iso.mjs`
 * 只抓源码字面量，`{index.asof}` 这种 payload 直出要靠 `e2e/noRawIso.spec.ts`
 * 在运行时页面文本上抓 —— 两份门禁的修复口都是本文件。
 */

/** 自然日毫秒数（只用日期粒度，故不受时区偏移影响）。 */
const DAY_MS = 86_400_000;

/** 取 ISO 的日期部分：`YYYY-MM-DD` 与完整时间戳（`2026-10-13T15:00:00Z`）都吃。 */
function dayPart(iso: string): string {
  return (iso ?? "").slice(0, 10);
}

/** ISO 日期 → 「9月9日」（与 features/market/components/format.ts 既有实现同形）。 */
export function formatCnDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : iso;
}

/**
 * `asOf` 日期部分 → 「10月13日」（不可解析时原样返回，与 `formatCnDate` 同约定）。
 * `FreshnessNote` 与 `formatAsOf` 共用它，保证"日期怎么写"只有一处实现。
 */
export function formatAsOfDate(iso: string): string {
  const [, month, day] = dayPart(iso).split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : iso;
}

/**
 * 距今天数（**本地日历日**粒度）：今天=0、昨天=1、未来=负数、不可解析=`null`。
 *
 * 用"把两个日期都归到各自的**本地**零点的 UTC 表示"做差，而不是
 * `(now - Date.parse(iso)) / DAY_MS`：后者会让"昨天 23:00 与今天 00:30"算成 0 天。
 * `asOf` 是**自然日**口径（`stale_days` 也按自然日），故必须按日粒度算。
 */
export function daysAgoOf(iso: string, now: Date = new Date()): number | null {
  const target = Date.parse(`${dayPart(iso)}T00:00:00Z`);
  if (Number.isNaN(target)) return null;
  const today = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((today - target) / DAY_MS);
}

/**
 * `as_of` → 「10月9日收盘 · 3天前」（§3.9 的契约形态）。
 *
 * 口径说明（**别拿它当实时时间的格式化器**）：
 * - 本入口只面向**日级收盘**的 `as_of`（`index_dailies` / 按日聚合端点），
 *   故固定带「收盘」二字；盘中/实时口径不得走这里（那类应显式写清"盘中 HH:MM"）。
 * - 相对日部分只在**过去**时给出（当天/未来不写「0天前」——那是噪声，且未来日
 *   本身是数据异常的信号，不该被格式化器吞掉）。
 * - `now` 可注入，便于断言固定日期；缺省取当前时间。
 */
export function formatAsOf(iso: string, now: Date = new Date()): string {
  const date = formatAsOfDate(iso);
  const days = daysAgoOf(iso, now);
  if (days === null || days <= 0) return `${date}收盘`;
  return `${date}收盘 · ${days}天前`;
}
