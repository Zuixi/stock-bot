import { apiDelete, apiGet, apiPost } from "./client";
import { mapBackendStock, type BackendStock } from "./stocks";
import type { StockRecord, TagSummary, UserTag } from "@/shared/types";

export function fetchStockUserTags(exchange: string, symbol: string): Promise<UserTag[]> {
  return apiGet<UserTag[]>(`/api/v1/exchanges/${exchange}/stocks/${symbol}/user-tags`);
}

export function addStockUserTag(
  exchange: string,
  symbol: string,
  tagName: string
): Promise<UserTag> {
  return apiPost<UserTag>(
    `/api/v1/exchanges/${exchange}/stocks/${symbol}/user-tags`,
    { tag_name: tagName }
  );
}

export function removeStockUserTag(
  exchange: string,
  symbol: string,
  tagName: string
): Promise<{ deleted: boolean }> {
  return apiDelete<{ deleted: boolean }>(
    `/api/v1/exchanges/${exchange}/stocks/${symbol}/user-tags/${encodeURIComponent(tagName)}`
  );
}

export function fetchAllTags(): Promise<TagSummary[]> {
  return apiGet<TagSummary[]>("/api/v1/tags");
}

export async function fetchStocksByTag(tagName: string): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStock[]>(
    `/api/v1/tags/${encodeURIComponent(tagName)}/stocks`
  );
  return rows.map(mapBackendStock);
}
