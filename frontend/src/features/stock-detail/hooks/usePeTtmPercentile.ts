import { useQuery } from "@tanstack/react-query";
import { fetchValuationHistory } from "@/shared/api/financial";
import type { Exchange } from "@/shared/types";

/**
 * 概览报价条用的 `PE_TTM 3 年分位`。
 *
 * **查询键必须与估值 tab 逐字一致**：`ValuationTab` 里 `useValuation(exchange, symbol, "pe_ttm", "3y")`
 * 生成 `["valuation-history", exchange, symbol, "pe_ttm", "3y"]`。键一致 ⇒ 概览先拉一次，
 * 切到估值 tab 命中缓存（不二次请求），两处数字也必然同源（不会一个 62% 一个 61%）。
 * 改这里任何一个字面量，都会让"切 tab 不重复请求"这条契约静默失效。
 */
export function peTtm3yQueryKey(exchange: Exchange | undefined, symbol: string | undefined) {
  return ["valuation-history", exchange, symbol, "pe_ttm", "3y"] as const;
}

export interface PeTtmPercentileState {
  /** 3 年分位（%）；`null` = 后端无有效样本（缺失 ≠ 0） */
  value: number | null;
  isError: boolean;
  retry: () => void;
}

export function usePeTtmPercentile(
  exchange: Exchange | undefined,
  symbol: string | undefined
): PeTtmPercentileState {
  const query = useQuery({
    queryKey: peTtm3yQueryKey(exchange, symbol),
    queryFn: () => fetchValuationHistory(exchange as Exchange, symbol as string, "pe_ttm", "3y"),
    enabled: Boolean(exchange && symbol),
  });

  return {
    value: query.data?.percentiles?.["3y"] ?? null,
    isError: query.isError,
    retry: () => void query.refetch(),
  };
}
