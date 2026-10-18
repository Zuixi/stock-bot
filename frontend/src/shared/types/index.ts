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

export interface SwChainNode {
  level: number;
  code: string;
  name: string;
}

export interface StockRecord {
  symbol: string;
  name: string;
  exchange: Exchange;
  category: string;
  industry?: string;
  swChain?: SwChainNode[];
  /**
   * 名录级身份字段（F17）：后端 `StockOut` 一直有，前端此前连类型都没映射，
   * 于是「公司概况」这块既有信息白丢。四个字段 `null` 都合法（缺失 ≠ 0），
   * 消费方必须**整项不渲染**而不是画 `--`（见 `CompanyProfile`）。
   */
  fullName?: string;
  /** 上市日期（`YYYY-MM-DD`，TuShare `stock_basic.list_date` 口径） */
  listDate?: string;
  /** 地区（TuShare `area`，通常是省份） */
  area?: string;
  /** 实控人（TuShare `act_name`）；国企多为机构名，民企可能是自然人 */
  actName?: string;
  latestPrice?: number;
  change?: number;
  changePercent?: number;
  volume?: number;
  turnover?: number;
  marketCap?: number;
  circulatingCap?: number;
  pe?: number;
  pb?: number;
  open?: number;
  high?: number;
  low?: number;
  prevClose?: number;
  turnoverRate?: number;
  detail?: Record<string, unknown>;
  asof: string;
  /** 最新行情的 trade_date（行情口径"数据截至"）；asof 是名录 ingest 时间，勿混用 */
  latestQuoteDate?: string;
}

export interface KLinePoint {
  date: string;
  open: number;
  close: number;
  high: number;
  low: number;
  volume: number;
  amount?: number;
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

export type AdjustMode = "raw" | "qfq";
export interface KlineResult { points: KLinePoint[]; adjustAvailable: boolean; }
export type KlineFetcher = (days: number, adjust: AdjustMode) => Promise<KlineResult>;
