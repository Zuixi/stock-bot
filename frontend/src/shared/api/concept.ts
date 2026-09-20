import { apiGet } from "./client";
import { mapBackendStockEnriched, type BackendStockEnriched } from "./stocks";
import type { StockRecord } from "@/shared/types";

export interface ConceptBoardItem {
  board_code: string;
  board_name: string;
  member_count: number;
  unresolved_count: number;
  priced_count: number;
  up_count: number;
  flat_count: number;
  down_count: number;
  avg_pct: number | null;
  main_net_inflow: number | null;
  main_net_ratio: number | null;
  lead_stock_name: string | null;
  lead_stock_code: string | null;
  lead_stock_pct: number | null;
  leaders: { symbol: string; name: string; change_percent: number | null }[];
}

export interface ConceptDetail {
  as_of: string;
  membership_as_of: string | null;
  source: string;
  degraded_reason: string | null;
  board: ConceptBoardItem;
  kpis: { zt_count: number; max_streak: number; leader_symbol: string | null; leader_name: string | null };
  echelons: Echelon[];
  unresolved_count: number;
  stock_count: number;
}

export interface SymbolConcept {
  board_code: string;
  board_name: string;
  pct_change: number | null;
}

export interface NewStockKpis {
  up_count: number;
  flat_count: number;
  down_count: number;
  unpriced_count: number;
  limit_up_count: number;
  unbroken_count: number;
  above_first_open_count: number;
  avg_pct: number | null;
}

export interface NewStockItem {
  symbol: string;
  name: string;
  exchange: string;
  list_date: string | null;
  listed_trade_days: number;
  pct_chg: number | null;
  close: number | null;
  turnover_rate: number | null;
  circ_mv: number | null;
  amount: number | null;
  streak: number | null;
  is_lu: boolean;
  never_broken: boolean | null;
  first_open: number | null;
  above_first_open: boolean | null;
}

export interface NewStocksResponse {
  as_of: string;
  membership_as_of: string | null;
  source: string;
  degraded_reason: string | null;
  /** `stock_id IS NULL` 的成分数（名录滞后披露）：既不在 items 也不进 KPI，>0 时卡片必须提示。 */
  unresolved_count: number;
  kpis: NewStockKpis;
  items: NewStockItem[];
}

/** 与后端 `app/schemas/limit_up.py:EchelonOut` 同形（板内梯队直接复用 `<LimitUpLadder>`）。 */
export interface EchelonStock {
  symbol: string;
  name: string;
  streak: number;
  days_span: number;
  boards_in_window: number;
  missing_days: number;
  amount?: number | null;
  seal_time?: string | null;
  seal_fund?: number | null;
  break_count?: number | null;
}

export interface Echelon {
  streak: number;
  label: string;
  stocks: EchelonStock[];
}

/**
 * `GET /api/v1/concepts` 的信封（与后端 `app/schemas/concept.py::ConceptListOut` 同键）。
 *
 * 与详情页不同，这里**不做 snake→camel 映射**：列表页的行就是 `ConceptBoardItem` 本身，
 * 多包一层只会让字段二次漂移。`as_of`（行情判据日）与 `membership_as_of`（成分快照日）是
 * 两个独立口径，列表页必须同时披露（历史口径声明是契约要求）。
 */
export interface ConceptListEnvelope {
  /** 行情（本地聚合）判据日；无行情时 null（配合 `degraded_reason`）。 */
  as_of: string | null;
  /** 全体成分行的 `max(last_seen_on)`：**成分**快照日，与 `as_of` 不是同一天。 */
  membership_as_of: string | null;
  price_source: "local_agg";
  /** 该 as_of 的东财资金流快照是否有行（数据集级）；无则 main_net_* 全为 null。 */
  flow_source: "em_clist" | null;
  /** 启用板块总数（与分页无关）。 */
  total: number;
  degraded_reason: string | null;
  items: ConceptBoardItem[];
}

/**
 * 概念板块列表（本地成分聚合，T-1/口径见 plans §2.2/§2.3）。
 *
 * 默认 `limit: 1000` 是刻意的：概念分类要「查看全部」（dev 库 504 板一次拉全），后端 `le=1000`
 * 与 `concept_service` 的全库聚合上限同量级。默认 `sort: "pct"` 保持 `avg_pct DESC NULLS LAST`。
 */
export function fetchConceptList(
  params: { sort?: "pct" | "inflow"; limit?: number; offset?: number } = {}
): Promise<ConceptListEnvelope> {
  const { sort = "pct", limit = 1000, offset = 0 } = params;
  return apiGet<ConceptListEnvelope>("/api/v1/concepts", { sort, limit, offset });
}

export function fetchConceptDetail(code: string) {
  return apiGet<ConceptDetail>(`/api/v1/concepts/${code}`);
}

export function fetchConceptStocks(code: string): Promise<StockRecord[]> {
  return apiGet<BackendStockEnriched[]>(`/api/v1/concepts/${code}/stocks`).then((r) =>
    r.map(mapBackendStockEnriched)
  );
}

export function fetchConceptsBySymbol(symbol: string) {
  return apiGet<{ as_of: string; membership_as_of: string | null; items: SymbolConcept[] }>(
    `/api/v1/concepts/by-symbol/${symbol}`
  );
}

export function fetchNewStocks(): Promise<NewStocksResponse> {
  return apiGet<NewStocksResponse>("/api/v1/new-stocks");
}
