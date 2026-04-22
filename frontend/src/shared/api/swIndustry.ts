import { apiGet, apiPut } from "./client";
import { mapBackendStock, type BackendStock } from "./stocks";
import type { StockRecord, SwTagOption, StockSwTag } from "@/shared/types";

export interface SwIndustryLevel3 {
  code: string;
  name: string;
  stockCount: number;
  symbols: string[];
}

export interface SwIndustryLevel2 {
  code: string;
  name: string;
  stockCount: number;
  children: SwIndustryLevel3[];
}

export interface SwIndustryLevel1 {
  code: string;
  name: string;
  stockCount: number;
  children: SwIndustryLevel2[];
}

export function fetchSwIndustryTree(): Promise<SwIndustryLevel1[]> {
  return apiGet<SwIndustryLevel1[]>("/api/v1/market/sw-industry/tree");
}

export async function fetchSwLevel1Stocks(level1Code: string): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStock[]>(`/api/v1/market/sw-industry/${level1Code}/stocks`);
  return rows.map(mapBackendStock);
}

export async function fetchSwLevel2Stocks(level1Code: string, level2Code: string): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStock[]>(
    `/api/v1/market/sw-industry/${level1Code}/${level2Code}/stocks`
  );
  return rows.map(mapBackendStock);
}

export async function fetchSwLevel3Stocks(
  level1Code: string,
  level2Code: string,
  level3Code: string
): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStock[]>(
    `/api/v1/market/sw-industry/${level1Code}/${level2Code}/${level3Code}/stocks`
  );
  return rows.map(mapBackendStock);
}

// ---------------------------------------------------------------------------
// SW industry options & custom tags
// ---------------------------------------------------------------------------

export function fetchSwOptions(level: 2 | 3): Promise<SwTagOption[]> {
  return apiGet<SwTagOption[]>("/api/v1/market/sw-industry/options", { level });
}

export function fetchStockSwTags(exchange: string, symbol: string): Promise<StockSwTag[]> {
  return apiGet<StockSwTag[]>(`/api/v1/exchanges/${exchange}/stocks/${symbol}/sw-tags`);
}

export function updateStockSwTags(
  exchange: string,
  symbol: string,
  industryCodes: string[]
): Promise<StockSwTag[]> {
  return apiPut<StockSwTag[]>(
    `/api/v1/exchanges/${exchange}/stocks/${symbol}/sw-tags`,
    { industry_codes: industryCodes }
  );
}
