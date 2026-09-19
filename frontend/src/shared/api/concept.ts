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
  leaders: { symbol: string; name: string; change_percent: number }[];
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
