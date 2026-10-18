import { useQuery } from "@tanstack/react-query";
import { fetchFinancialSummary } from "@/shared/api/financial";
import type { Exchange } from "@/shared/types";

/**
 * 财务摘要的新鲜度：5 分钟。
 *
 * 全局默认是 `App.tsx` 的 `staleTime: 30_000`（30 秒），本 hook 把它从 **30s 放宽到 5min**：
 * 财报是季度级数据，30 秒的"新鲜度"没有信息价值，却会让页内来回切 tab 时在
 * `refetchOnMount` 下静默重拉一次。放宽之后，"切财务 tab 命中缓存、请求数仍为 1"
 * （`e2e/stockDetailFinancial.spec.ts` 的判据）才是稳定契约，而不是一场 30 秒竞速。
 *
 * 命名沿用本 feature 内其它查询模块的 `STALE_TIME` 常量写法
 * （`CustomSwTags.tsx` / `UserTags.tsx` / `RelatedEvents.tsx`）。
 */
const STALE_TIME = 5 * 60 * 1000;

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
    staleTime: STALE_TIME,
  });
}
