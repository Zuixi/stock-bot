import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKlineBySymbol } from "@/shared/api/quotes";
import {
  RANGE_FETCH_DAYS,
  RANGE_WINDOW,
  computeStockRangeStats,
  type StockRangeStats,
} from "./rangeStats";

export { RANGE_FETCH_DAYS, RANGE_WINDOW, computeStockRangeStats };
export type { StockRangeStats };

export interface StockRangeStatsState {
  /** `null` = 样本不足 / 区间退化（整条不渲染）；**不是** 0 */
  stats: StockRangeStats | null;
  /** 取数失败（与"没有 52 周数据"是两件事，见设计稿 §3） */
  isError: boolean;
  retry: () => void;
}

/**
 * 个股 52 周区间统计，**零新增请求**。
 *
 * ### R7：为什么不读 `KlineChart` 的 `adjust`
 * `KlineChart` 把复权开关放在自己内部（`useState<AdjustMode>`），没有对外暴露，父组件拿不到。
 * 为了不改造它、也不把同一个状态复制一份（两份状态必然漂移），这里把区间统计**钉死在 `qfq`**，
 * 并把查询键写成与 `KlineChart` 在 qfq 生效时**逐字相同**的形状：
 *
 *   `["kline", `stock-kline-${symbol}`, "qfq"]`  （见 `KlineChart.tsx` 的
 *   `queryKey: ["kline", queryKey, adjust]` + 页面传入的 `queryKey={stock-kline-<symbol>}`）
 *
 * 键相同 ⇒ react-query 命中同一份缓存（首屏并发时还会合并成同一个在途请求），
 * e2e `stockDetailLayout.spec.ts` 用「`/quotes/daily` 的请求数 == 1」把这条钉住。
 *
 * ### 已知代价（有意接受，不是 bug）
 * 复权因子尚未回补（`adjustAvailable === false`）时 `KlineChart` 会落到 `raw`，两者键不同：
 * 此时本 hook 会自己拉一次 qfq 日线，页面出现 2 次 `/quotes/daily`，且区间口径（qfq）与图上的
 * 「不复权」形态不一致。窗口很短（后台回补完成后自动一致），换来的是「统计不读组件内部状态」。
 * 若将来 `KlineChart` 把 `adjust` 提升为受控 prop，这里应改为消费同一份状态并删掉本注释。
 */
export function useStockRangeStats(symbol: string | undefined): StockRangeStatsState {
  const query = useQuery({
    queryKey: ["kline", `stock-kline-${symbol ?? ""}`, "qfq"] as const,
    queryFn: () => fetchKlineBySymbol(symbol as string, RANGE_FETCH_DAYS, "qfq"),
    enabled: Boolean(symbol),
    // 与 KlineChart 的 STALE_TIME 一致：同一份数据不该有两个新鲜度
    staleTime: 5 * 60 * 1000,
  });

  const stats = useMemo(() => computeStockRangeStats(query.data?.points ?? []), [query.data]);

  return { stats, isError: query.isError, retry: () => void query.refetch() };
}
