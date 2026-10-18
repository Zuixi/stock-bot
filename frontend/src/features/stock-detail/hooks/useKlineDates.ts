import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchKlineBySymbol } from "@/shared/api/quotes";
import { RANGE_FETCH_DAYS } from "./rangeStats";

const STALE_TIME = 5 * 60 * 1000;

/**
 * 已加载的**日线 bar 日期**（升序），用于把事件映射到 K 线的下标。
 *
 * 零新增请求（R7，与 `useStockRangeStats` 同一套论证）：查询键**钉字面量**成
 * `["kline", `stock-kline-${symbol}`, "qfq"]`，与 `KlineChart` 在 qfq 生效时的
 * `["kline", queryKey, adjust]`、以及 `useStockRangeStats` 的键**逐字相同** ⇒
 * 命中同一份缓存（首屏并发时合并成同一个在途请求）。`stockDetailLayout.spec.ts`
 * 用「`/quotes/daily` 恰好 1 次」把这条钉住。
 *
 * 之所以页面要这一份日期：`toEventMarkers` / `indexOfDate` 都是**纯函数**，
 * 入参必须是真实的 bar 日期（事件日期不在其中 = 上市前/停牌 ⇒ 不画标记、点击 no-op）。
 * 把日期从 `KlineChart` 内部掏出来需要给它加回调/受控状态，改动面更大且会把
 * 频率聚合状态外泄；复用缓存键是既有先例（见 `useStockRangeStats` 的 R7 注释）。
 *
 * 频率切到周/月后 x 轴是聚合后的日期，事件日期通常不再精确命中 ⇒ 该频率下不画标记。
 * 这是有意的：把"10-13 的公告"硬贴到"10-10 那周"的蜡烛上会读成另一天。
 */
export function useKlineDates(symbol: string | undefined): string[] {
  const query = useQuery({
    queryKey: ["kline", `stock-kline-${symbol ?? ""}`, "qfq"] as const,
    queryFn: () => fetchKlineBySymbol(symbol as string, RANGE_FETCH_DAYS, "qfq"),
    enabled: Boolean(symbol),
    staleTime: STALE_TIME,
  });

  // 稳定引用：`useMemo` 让下游 `toEventMarkers` / option 的 memo 不因每次渲染重建
  return useMemo(() => (query.data?.points ?? []).map((p) => p.date), [query.data]);
}
