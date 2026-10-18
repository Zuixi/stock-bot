import { useQuery } from "@tanstack/react-query";
import { fetchFinancialSummary } from "@/shared/api/financial";
import type { Exchange } from "@/shared/types";

/**
 * `GET /financial-summary` 的**唯一**查询入口（概览「成长与盈利」卡 + 财务 tab 共用）。
 *
 * 单一 query key `["financial-summary", exchange, symbol]` 是这次改造的核心契约：
 * 两处若各写一个字面量，key 一旦漂移就会在切 tab 时二次请求（同一个后端响应打两遍），
 * 且概览卡与财务摘要可能落在**不同报告期**上。故 key 只在这里生成，两处都调本 hook。
 */
export function financialSummaryQueryKey(exchange: Exchange | undefined, symbol: string | undefined) {
  return ["financial-summary", exchange, symbol] as const;
}

export function useFinancialSummary(exchange: Exchange | undefined, symbol: string | undefined) {
  return useQuery({
    queryKey: financialSummaryQueryKey(exchange, symbol),
    queryFn: () => fetchFinancialSummary(exchange as Exchange, symbol as string),
    enabled: Boolean(exchange && symbol),
    // 财报数据变动极慢（季度级）：同一页内切 tab 命中缓存即可，避免二次请求。
    // 全局默认 30s 已够，但这里显式钉住意图，防止将来调低全局 staleTime 时回归。
    staleTime: 5 * 60_000,
  });
}
