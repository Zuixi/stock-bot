import { apiGet } from "./client";
import type { Exchange, StockRecord } from "@/shared/types";

interface BackendPagedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

interface BackendCategory {
  exchange: Exchange;
  category: string;
  count: number;
}

export interface BackendStock {
  exchange: Exchange;
  symbol: string;
  name: string;
  full_name?: string | null;
  category: string;
  list_date?: string | null;
  csrc_code?: string | null;
  csrc_desc?: string | null;
  province?: string | null;
  status?: string | null;
  detail?: Record<string, unknown> | null;
  asof: string;
}

const EXCHANGES: Exchange[] = ["Shanghai_Stocks", "Shenzen_Stocks", "Beijing_Stocks"];

export function mapBackendStock(item: BackendStock): StockRecord {
  return {
    exchange: item.exchange,
    symbol: item.symbol,
    name: item.name,
    category: item.category,
    industry: item.csrc_desc ?? undefined,
    asof: item.asof,
    // Backend M0 currently does not provide quote/fundamental fields yet.
    latestPrice: undefined,
    change: undefined,
    changePercent: undefined,
    volume: undefined,
    turnover: undefined,
    marketCap: undefined,
    circulatingCap: undefined,
    pe: undefined,
    pb: undefined,
    roe: undefined,
    revenueGrowth: undefined,
    profitGrowth: undefined,
    detail: item.detail ?? undefined,
  };
}

export async function fetchStocksByExchange(params: {
  exchange: Exchange;
  category?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}): Promise<BackendPagedResponse<StockRecord>> {
  const response = await apiGet<BackendPagedResponse<BackendStock>>(
    `/api/v1/exchanges/${params.exchange}/stocks`,
    {
      category: params.category,
      keyword: params.keyword,
      page: params.page ?? 1,
      page_size: params.page_size ?? 200,
    }
  );
  return {
    ...response,
    items: response.items.map(mapBackendStock),
  };
}

export async function fetchStocksMerged(params: {
  exchange?: Exchange;
  category?: string;
  keyword?: string;
  page?: number;
  page_size?: number;
}): Promise<BackendPagedResponse<StockRecord>> {
  const pageSize = params.page_size ?? 100;
  const response = await apiGet<BackendPagedResponse<BackendStock>>(
    "/api/v1/exchanges/stocks",
    {
      exchange: params.exchange,
      category: params.category,
      keyword: params.keyword,
      page: params.page ?? 1,
      page_size: pageSize,
    }
  );
  return {
    ...response,
    items: response.items.map(mapBackendStock),
  };
}

export async function fetchStockBySymbol(symbol: string): Promise<StockRecord | null> {
  for (const exchange of EXCHANGES) {
    try {
      const item = await apiGet<BackendStock>(`/api/v1/exchanges/${exchange}/stocks/${symbol}`);
      return mapBackendStock(item);
    } catch {
      // try next exchange
    }
  }
  return null;
}

export async function fetchCategories(exchange?: Exchange): Promise<BackendCategory[]> {
  const response = await apiGet<BackendCategory[]>("/api/v1/exchanges/categories", {
    exchange,
  });
  return response;
}
