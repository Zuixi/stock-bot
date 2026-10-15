/**
 * A股 7 榜（spec §S1.6）的数据获取与形状映射。
 *
 * 两条纪律：
 *  1. **口径不在这里编**：`caliber`（交易日 / 财务期 / 样本范围）与 `notes`
 *     （不足 100 条的原因）由后端下发，前端只做展示，不在 UI 层写死任何口径数字；
 *  2. **缺失渲染 `--`，不回退 0**：财务字段为 `null` 表示"该交易日尚无已公告期"，
 *     与"利润为 0"是两件事（见 `docs/decisions/0011-point-in-time-data-contract.md`）。
 */
import { apiGet } from "@/shared/api/client";

/** 7 个榜的 key（与后端 `RankingBoardKey` 逐字一致）。 */
export type RankingBoardKey =
  | "market_cap"
  | "dividend_yield"
  | "monthly_gain"
  | "monthly_loss"
  | "half_year_profit"
  | "annual_profit"
  | "roe";

export type RankingBoardCategory = "valuation" | "price" | "financial";

/** 榜单的数据来源家族：决定"用的是哪期财务数据"这一栏是否有意义。 */
export type RankingBoardSource = "daily_basic" | "daily_quotes_window" | "financial_statement";

/** 逐行携带的 PIT 出处（`ann_date_source` 是判别式，不是可选文案）。 */
export interface FinancialReportRef {
  endDate: string;
  reportType: string;
  annDate: string;
  annDateSource: "f_ann_date" | "ann_date";
}

export interface RankingBoardItem {
  rank: number;
  symbol: string;
  name: string;
  exchange?: string | null;
  close?: number | null;
  pct_chg?: number | null;
  total_mv?: number | null;
  dv_ratio?: number | null;
  monthly_pct_chg?: number | null;
  n_income_attr_p?: number | null;
  roe?: number | null;
  is_st: boolean;
  is_new_stock: boolean;
  financial_report?: FinancialReportRef | null;
}

export interface RankingCaliber {
  /** ① 以哪个交易日为准 */
  tradeDate: string;
  /** ② 用的是哪期财务数据（纯行情/估值榜为「不适用（…）」） */
  financialPeriod: string;
  /** ③ 是否含次新股/ST */
  sampleScope: string;
}

export interface RankingBoardEnvelope {
  asOf: string;
  asOfQuality: string;
  asOfReason: string | null;
  isLatestTradingDay: boolean;
  key: RankingBoardKey;
  label: string;
  category: RankingBoardCategory;
  source: RankingBoardSource;
  sortDirection: "DESC" | "ASC";
  metricUnit: string;
  limit: number;
  returnedRows: number;
  eligibleRows: number;
  truncatedTies: boolean;
  notes: string[];
  caliber: RankingCaliber;
  items: RankingBoardItem[];
}

interface BackendFinancialReportRef {
  end_date: string;
  report_type: string;
  ann_date: string;
  ann_date_source: "f_ann_date" | "ann_date";
}

interface BackendRankingBoardItem extends Omit<RankingBoardItem, "financial_report"> {
  financial_report?: BackendFinancialReportRef | null;
}

interface BackendRankingBoardResponse {
  as_of: string;
  as_of_quality?: string;
  as_of_reason?: string | null;
  is_latest_trading_day: boolean;
  key: RankingBoardKey;
  label: string;
  category: RankingBoardCategory;
  source: RankingBoardSource;
  sort_direction: "DESC" | "ASC";
  metric_unit: string;
  limit: number;
  returned_rows: number;
  eligible_rows: number;
  truncated_ties: boolean;
  notes: string[];
  caliber: { trade_date: string; financial_period: string; sample_scope: string };
  items: BackendRankingBoardItem[];
}

/**
 * 7 榜的**展示顺序与分组**（Tab 结构与顺序的唯一真相）。
 *
 * 只放 key 与分组，**不放口径**——口径一律来自后端 payload，
 * 这样后端改了取期规则，UI 不会继续宣称旧口径。
 */
export const RANKING_BOARD_TABS: ReadonlyArray<{
  key: RankingBoardKey;
  label: string;
  category: RankingBoardCategory;
}> = [
  { key: "market_cap", label: "市值", category: "valuation" },
  { key: "dividend_yield", label: "股息率", category: "valuation" },
  { key: "monthly_gain", label: "月度涨幅", category: "price" },
  { key: "monthly_loss", label: "月度跌幅", category: "price" },
  { key: "half_year_profit", label: "半年利润", category: "financial" },
  { key: "annual_profit", label: "年利润", category: "financial" },
  { key: "roe", label: "ROE", category: "financial" },
];

export const RANKING_CATEGORY_LABEL: Record<RankingBoardCategory, string> = {
  valuation: "估值",
  price: "行情",
  financial: "财务",
};

export function isRankingBoardKey(value: string | undefined): value is RankingBoardKey {
  return RANKING_BOARD_TABS.some((tab) => tab.key === value);
}

/** 榜单默认 100 强（后端上限也是 100）。 */
export const RANKING_BOARD_LIMIT = 100;

export function fetchRankingBoard(
  key: RankingBoardKey,
  limit = RANKING_BOARD_LIMIT,
): Promise<RankingBoardEnvelope> {
  return apiGet<BackendRankingBoardResponse>(
    `/api/v1/market/rankings/boards/${key}?limit=${limit}`,
  ).then((b) => ({
    asOf: b.as_of,
    // 口径未知时不得谎报「收盘」（与既有榜单同一处置）
    asOfQuality: b.as_of_quality ?? "partial",
    asOfReason: b.as_of_reason ?? null,
    isLatestTradingDay: b.is_latest_trading_day,
    key: b.key,
    label: b.label,
    category: b.category,
    source: b.source,
    sortDirection: b.sort_direction,
    metricUnit: b.metric_unit,
    limit: b.limit,
    returnedRows: b.returned_rows,
    eligibleRows: b.eligible_rows,
    truncatedTies: b.truncated_ties,
    notes: b.notes ?? [],
    caliber: {
      tradeDate: b.caliber.trade_date,
      financialPeriod: b.caliber.financial_period,
      sampleScope: b.caliber.sample_scope,
    },
    items: (b.items ?? []).map((item) => ({
      ...item,
      is_st: Boolean(item.is_st),
      is_new_stock: Boolean(item.is_new_stock),
      financial_report: item.financial_report
        ? {
            endDate: item.financial_report.end_date,
            reportType: item.financial_report.report_type,
            annDate: item.financial_report.ann_date,
            annDateSource: item.financial_report.ann_date_source,
          }
        : null,
    })),
  }));
}
