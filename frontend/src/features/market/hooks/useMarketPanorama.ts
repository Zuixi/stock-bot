import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { fetchMarketPanorama } from "@/shared/api/panorama";

/**
 * 全景日表读面：**不轮询**（日级数据，盘中不会变）。
 * 切 20/60/120/250 时用 `keepPreviousData` 保留上一帧，避免闪骨架/布局跳动。
 */
export function useMarketPanorama(days: number) {
  return useQuery({
    queryKey: ["market", "panorama", days],
    queryFn: () => fetchMarketPanorama(days),
    staleTime: 60_000,
    placeholderData: keepPreviousData,
  });
}
