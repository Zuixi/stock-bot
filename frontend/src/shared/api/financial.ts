import { apiGet } from "./client";
import type { Exchange } from "@/shared/types";

// ---------------------------------------------------------------------------
// 类型：与后端 financial / valuation 接口返回结构对应
// ---------------------------------------------------------------------------

export type MetricQuality = "reported" | "derived" | string;

export interface FinancialMetricValue {
  value: number | null;
  unit?: string | null;
  period_type?: string | null;
  calc_method?: string | null;
  quality?: MetricQuality | null;
  source?: string | null;
}

export interface FinancialSummary {
  exchange: Exchange;
  symbol: string;
  name: string;
  latest_period: string | null;
  report_type: string | null;
  ann_date: string | null;
  as_of: string | null;
  source: string | null;
  metrics: Record<string, FinancialMetricValue | undefined>;
}

export interface StatementPeriodMeta {
  period: string;
  report_type: string | null;
  ann_date: string | null;
  quality?: string | null;
  source?: string | null;
}

export interface IncomeStatementRow {
  period: string;
  revenue?: number | null;
  operate_cost?: number | null;
  operate_profit?: number | null;
  total_profit?: number | null;
  n_income?: number | null;
  n_income_attr_p?: number | null;
  deduct_n_income?: number | null;
  sell_exp?: number | null;
  admin_exp?: number | null;
  fin_exp?: number | null;
  rd_exp?: number | null;
  basic_eps?: number | null;
  diluted_eps?: number | null;
}

export interface BalanceSheetRow {
  period: string;
  total_assets?: number | null;
  total_liab?: number | null;
  total_hldr_eqy_exc_min_int?: number | null;
  total_hldr_eqy_inc_min_int?: number | null;
  money_cap?: number | null;
  accounts_receiv?: number | null;
  inventories?: number | null;
  fix_assets?: number | null;
  intan_assets?: number | null;
  st_borrow?: number | null;
  lt_borrow?: number | null;
}

export interface CashFlowRow {
  period: string;
  n_cashflow_act?: number | null;
  n_cashflow_inv_act?: number | null;
  n_cashflow_fin_act?: number | null;
  c_cash_equ_end_period?: number | null;
}

export interface FinancialStatements {
  exchange: Exchange;
  symbol: string;
  name: string;
  periods: StatementPeriodMeta[];
  income_statement: IncomeStatementRow[];
  balance_sheet: BalanceSheetRow[];
  cash_flow: CashFlowRow[];
}

export interface MetricHistoryPoint {
  period: string;
  value: number | null;
  unit: string | null;
  quality?: string | null;
}

export interface MetricHistory {
  metric_key: string;
  unit: string | null;
  points: MetricHistoryPoint[];
}

export type ValuationMetric =
  | "pe_ttm"
  | "pe"
  | "pb"
  | "ps_ttm"
  | "ps"
  | "dividend_yield";

export type ValuationRange = "1y" | "3y" | "5y";

export interface ValuationPercentiles {
  "1y"?: number | null;
  "3y"?: number | null;
  "5y"?: number | null;
}

export interface ValuationHistory {
  exchange: Exchange;
  symbol: string;
  metric: string;
  range: string;
  as_of: string | null;
  current: number | null;
  percentiles: ValuationPercentiles;
  stats: { min: number | null; median: number | null; max: number | null; sample_count: number };
  series: { trade_date: string; value: number | null }[];
}

// ---------------------------------------------------------------------------
// 请求函数：路径前缀统一为 /api/v1/exchanges/{exchange}/stocks/{symbol}
// ---------------------------------------------------------------------------

function stockPath(exchange: Exchange, symbol: string): string {
  return `/api/v1/exchanges/${exchange}/stocks/${symbol}`;
}

/** GET /financial-summary */
export function fetchFinancialSummary(
  exchange: Exchange,
  symbol: string
): Promise<FinancialSummary> {
  return apiGet<FinancialSummary>(`${stockPath(exchange, symbol)}/financial-summary`);
}

/** GET /financial-statements?period_count=N */
export function fetchFinancialStatements(
  exchange: Exchange,
  symbol: string,
  periodCount: number
): Promise<FinancialStatements> {
  return apiGet<FinancialStatements>(`${stockPath(exchange, symbol)}/financial-statements`, {
    period_count: periodCount,
  });
}

/** GET /financial-metrics/history?metric_keys=a,b,c */
export function fetchFinancialMetricsHistory(
  exchange: Exchange,
  symbol: string,
  metricKeys: string[]
): Promise<MetricHistory[]> {
  return apiGet<MetricHistory[]>(
    `${stockPath(exchange, symbol)}/financial-metrics/history`,
    { metric_keys: metricKeys.join(",") }
  );
}

/** GET /valuation-history?metric=pe_ttm&range=3y */
export function fetchValuationHistory(
  exchange: Exchange,
  symbol: string,
  metric: ValuationMetric,
  range: ValuationRange
): Promise<ValuationHistory> {
  return apiGet<ValuationHistory>(`${stockPath(exchange, symbol)}/valuation-history`, {
    metric,
    range,
  });
}
