import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKlineBySymbol } from "@/shared/api/quotes";
import { RANGE_FETCH_DAYS } from "./rangeStats";
import { characterStats, type CharacterStats } from "../lib/characterStats";

export interface CharacterStatsState {
  /** `null` = 样本不足/价格不可用（整行不渲染）；**不是** 0 */
  stats: CharacterStats | null;
  /** 取数失败（与"样本不足"是两件事，见组件内的失败态说明） */
  isError: boolean;
  retry: () => void;
}

/**
 * 「股性事实行」的取数编排，**零新增请求**。
 *
 * ### R7（与 `useStockRangeStats` 同一条复用契约）
 * 查询键写成与 `KlineChart` 在 qfq 下**逐字相同**的形状：
 *
 *   `["kline", `stock-kline-${symbol}`, "qfq"]`  （见 `KlineChart.tsx` 的
 *   `queryKey: ["kline", queryKey, adjust]` + 页面传入的 `queryKey={stock-kline-<symbol>}`）
 *
 * 键相同 ⇒ 与 K 线图、52 周区间统计命中**同一份缓存**（首屏并发时还会合并成同一个在途请求）。
 * 这一点被 `e2e/stockDetailCompany.spec.ts` 用「`/quotes/daily` 的请求数 == 1」钉住 ——
 * 键漂移一个字符就会变成 3 次，是本仓库最容易静默复发的缺陷类型。
 *
 * `queryFn` 的 `days` 也必须逐字相同（`RANGE_FETCH_DAYS = 3650`）：键只决定"命中谁"，
 * 而两个观察者中先注册的那个的 `queryFn` 才是真正发出去的那一个 —— 若这里的窗口更短，
 * 同一份缓存会因"谁先挂载"而给出不同的数据量，股性行的 60 根就成了掷骰子。
 *
 * ### 已知代价（有意接受，与 useStockRangeStats 同）
 * 复权因子尚未回补（`adjustAvailable === false`）时 `KlineChart` 会落到 `raw`，两者键不同：
 * 此时本 hook 会自己拉一次 qfq 日线，页面出现 2 次 `/quotes/daily`。窗口很短，
 * 换来的是"统计不读组件内部状态"。若 `adjust` 将来提升为受控 prop，这里应改为消费同一份状态。
 */
export function useCharacterStats(symbol: string | undefined): CharacterStatsState {
  const query = useQuery({
    queryKey: ["kline", `stock-kline-${symbol ?? ""}`, "qfq"] as const,
    queryFn: () => fetchKlineBySymbol(symbol as string, RANGE_FETCH_DAYS, "qfq"),
    enabled: Boolean(symbol),
    // 与 KlineChart / useStockRangeStats 一致：同一份数据不该有两个新鲜度
    staleTime: 5 * 60 * 1000,
  });

  const stats = useMemo(() => characterStats(query.data?.points ?? []), [query.data]);

  return { stats, isError: query.isError, retry: () => void query.refetch() };
}
