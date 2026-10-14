import { apiGet, apiPut } from "./client";
import {
    mapBackendStock,
    mapBackendStockEnriched,
    type BackendStock,
    type BackendStockEnriched,
} from "./stocks";
import type { StockRecord, SwTagOption, StockSwTag } from "@/shared/types";

// ---------------------------------------------------------------------------
// SW industry tree & levels
// ---------------------------------------------------------------------------

/**
 * 树节点的成分计数（三层同形）。
 *
 * **显示一律用 `officialCount`**：它是 SCD2 `sw_industry_member_periods.is_current` 的官方归属，
 * 与下钻页 `/market/sw-industry/drill/*` 的 `member_count` 逐节点一致（实测 511/511 差异 0）。
 * `stockCount` = `officialCount` + `customCount`，其中 `customCount` 是**用户自定义标签**
 * （`stock_custom_sw_tags`，挂在 L3/L2 上会被上卷到 L1）——它**不是**官方成分，
 * 混进"这个行业有多少只股票"会让数字无法对上上游、也对不上下钻页。
 * 需要时把 `customCount` 作为**带标注的次要信息**呈现（如「+7 自定义」），不要相加后当官方口径显示。
 */
export interface SwIndustryLevel3 {
  code: string;
  name: string;
  stockCount: number;
  officialCount: number;
  customCount: number;
  children: SwIndustryLevel3[];
}

export interface SwIndustryLevel2 {
  code: string;
  name: string;
  stockCount: number;
  officialCount: number;
  customCount: number;
  children: SwIndustryLevel3[];
}

export interface SwIndustryLevel1 {
  code: string;
  name: string;
  stockCount: number;
  officialCount: number;
  customCount: number;
  children: SwIndustryLevel2[];
}

/**
 * 用户自定义标签的**带标注后缀**，如 `+7 自定义`；无标签时返回空串。
 *
 * 为什么单独一个函数：这 4 处（网格瓦片 / 行业分类卡片 / L1 下钻的 L2 卡片 / L2 下钻的 L3 名）
 * 都要"官方数 + 可选的自定义标注"，抄 4 遍条件必然漂移。**它绝不参与官方数的相加**——
 * 自定义标签是"我的偏好"，不是"申万成分"，混进去数字就对不上上游、也对不上下钻页。
 */
export function customTagSuffix(node: { customCount?: number }): string {
  const n = node.customCount ?? 0;
  return n > 0 ? `+${n} 自定义` : "";
}

export function fetchSwIndustryTree(): Promise<SwIndustryLevel1[]> {
  return apiGet<SwIndustryLevel1[]>("/api/v1/market/sw-industry/tree");
}

// ── Fast (basic stock metadata) ──────────────────────────────────────────

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

// ── Enriched (basic + quote + daily_basic) — lazy-loaded after basic ─────

export async function fetchSwLevel1StocksEnriched(level1Code: string): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStockEnriched[]>(
    `/api/v1/market/sw-industry/${level1Code}/stocks/enriched`
  );
  return rows.map(mapBackendStockEnriched);
}

export async function fetchSwLevel2StocksEnriched(level1Code: string, level2Code: string): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStockEnriched[]>(
    `/api/v1/market/sw-industry/${level1Code}/${level2Code}/stocks/enriched`
  );
  return rows.map(mapBackendStockEnriched);
}

export async function fetchSwLevel3StocksEnriched(
  level1Code: string,
  level2Code: string,
  level3Code: string
): Promise<StockRecord[]> {
  const rows = await apiGet<BackendStockEnriched[]>(
    `/api/v1/market/sw-industry/${level1Code}/${level2Code}/${level3Code}/stocks/enriched`
  );
  return rows.map(mapBackendStockEnriched);
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
