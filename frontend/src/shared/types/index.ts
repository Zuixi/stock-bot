export interface ApiResponse<T> {
  data: T;
  asof: string;
  source?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}

export interface ListParams {
  page?: number;
  page_size?: number;
  sort_by?: string;
  sort_order?: "asc" | "desc";
  filters?: Record<string, string>;
}

export type Exchange = "Shanghai_Stocks" | "Shenzen_Stocks" | "Beijing_Stocks";

export const EXCHANGE_LABELS: Record<Exchange, string> = {
  Shanghai_Stocks: "上交所",
  Shenzen_Stocks: "深交所",
  Beijing_Stocks: "北交所",
};

export interface MarketIndex {
  code: string;
  tsCode: string;
  name: string;
  value: number;
  change: number;
  changePercent: number;
  exchange: Exchange | string;
  asof: string;
}

export interface StockRecord {
  symbol: string;
  name: string;
  exchange: Exchange;
  category: string;
  industry?: string;
  latestPrice?: number;
  change?: number;
  changePercent?: number;
  volume?: number;
  turnover?: number;
  marketCap?: number;
  circulatingCap?: number;
  pe?: number;
  pb?: number;
  roe?: number;
  revenueGrowth?: number;
  profitGrowth?: number;
  detail?: Record<string, unknown>;
  asof: string;
}

export interface KLinePoint {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
}

export interface SectorSummary {
  name: string;
  changePercent: number;
  totalMarketCap: number;
  stockCount: number;
  topStocks: Pick<StockRecord, "symbol" | "name" | "changePercent">[];
}

export interface SseSnapshot {
  code: string;
  name: string;
  last: number;
  prev_close: number | null;
  open: number | null;
  high: number | null;
  low: number | null;
  chg_rate: number | null;
  collect_time: string;
  trade_date: string;
}

export interface SseIntradayPoint {
  time: string;
  last: number;
  chg_rate: number | null;
}

export interface SseIntradayResponse {
  code: string;
  name: string;
  trade_date: string;
  data: SseIntradayPoint[];
}

// ---------------------------------------------------------------------------
// SW custom tags
// ---------------------------------------------------------------------------

export interface SwTagOption {
  code: string;
  name: string;
  parentCode?: string;
}

export interface StockSwTag {
  industryCode: string;
  industryName: string;
  level: number;
}

// ---------------------------------------------------------------------------
// User-defined custom tags
// ---------------------------------------------------------------------------

export interface UserTag {
  tag_name: string;
  created_at: string;
}

export interface TagSummary {
  tag_name: string;
  stock_count: number;
}
