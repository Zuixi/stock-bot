import type { GlobalIndexCard } from "@/shared/api/marketData";

/**
 * A 股核心指数契约顺序（契约 `docs/design/landing-market-theme.md` §4：
 * 上证/深证/创业板/沪深300/中证500/科创50）。宣传页指数条与市场页核心指数卡
 * 共用本清单——两处曾各自复制同一份 6 码数组与选择算法，改一处会静默漏另一处。
 */
export const CORE_TS_CODES = [
  "000001.SH", // 上证指数
  "399001.SZ", // 深证成指
  "399006.SZ", // 创业板指
  "000300.SH", // 沪深300
  "000905.SH", // 中证500
  "000688.SH", // 科创50
] as const;

/**
 * 优先按 {@link CORE_TS_CODES} 顺序取核心指数，缺谁用「其余」顺位补齐，恒定截断到
 * `count` 格。
 *
 * 「其余」的过滤条件是**调用方输入**而非写死：宣传页指数条接受任意市场的非核心
 * 指数补齐，市场页核心指数卡只允许 A 股（`market === "CN"`）补齐——保留两侧既有行为。
 * 传入 `undefined` 表示不过滤（全部非核心指数均可补齐）。
 */
export function pickCoreIndices(
  list: GlobalIndexCard[],
  count: number,
  isEligibleRest: (index: GlobalIndexCard) => boolean = () => true,
): GlobalIndexCard[] {
  const coreSet: ReadonlySet<string> = new Set(CORE_TS_CODES);
  const core = CORE_TS_CODES.map((code) => list.find((i) => i.tsCode === code)).filter(
    (i): i is GlobalIndexCard => !!i,
  );
  const rest = list.filter((i) => !coreSet.has(i.tsCode) && isEligibleRest(i));
  return [...core, ...rest].slice(0, count);
}
