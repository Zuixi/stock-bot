import { apiGet } from "./client";
import {
  mapMarketList,
  type AsOfQuality,
  type BackendMarketListOut,
  type MarketListEnvelope,
} from "./marketEnvelope";
import type { KLinePoint, KlineResult, MarketIndex, SectorSummary, SseIntradayResponse } from "@/shared/types";

export interface DistributionItem {
  range: string;
  count: number;
}

export interface CapitalFlowItem {
  name: string;
  inflow: number;
  outflow: number;
}

export interface HotBoardLeader {
  symbol: string;
  name: string;
  changePercent: number;
}

export interface HotBoardItem {
  id: string;
  name: string;
  code: string;
  changePercent: number;
  upCount: number;
  flatCount: number;
  downCount: number;
  leaders: HotBoardLeader[];
}

export type HotBoardCategory = "industry" | "concept" | "region";

// ---------------------------------------------------------------------------
// 公开榜单（Task 2.7，切到专用 /market/rankings 端点）
// ---------------------------------------------------------------------------

export type RankingType = "gainers" | "losers" | "amount" | "turnover_rate" | "volume";

export interface RankingItem {
  symbol: string;
  name: string;
  exchange?: string | null;
  close?: number | null;
  pct_chg?: number | null;
  /** 成交额，TuShare 原生 千元（与 StockEnrichedOut.amount 同口径，消费端 ×1000 → 元）。 */
  amount?: number | null;
  volume?: number | null;
  turnover_rate?: number | null;
  total_mv?: number | null;
}

export interface RankingResponse {
  as_of: string;
  /** 判据日完整性口径（后端 Task 2 起返回）；Phase 1 徽标 / query key 消费。 */
  as_of_quality: AsOfQuality;
  as_of_reason: string | null;
  is_latest_trading_day: boolean;
  type: RankingType;
  items: RankingItem[];
}

export function fetchRankings(type: RankingType, limit = 10): Promise<RankingResponse> {
  return apiGet<RankingResponse>(`/api/v1/market/rankings?type=${type}&limit=${limit}`);
}

interface IndexKlineResponse {
  ts_code: string;
  name: string;
  data: {
    trade_date: string;
    open: number | null;
    high: number | null;
    low: number | null;
    close: number;
    volume: number | null;
    amount?: number | null;
  }[];
}

export function fetchMarketIndices(): Promise<MarketIndex[]> {
  return apiGet<MarketIndex[]>("/api/v1/market/indices");
}

/**
 * 涨跌分布。后端返回 `MarketListOut` 信封，mapper 解包后返回 `{items, asOf, asOfQuality, asOfReason}`，
 * 消费端读 `.items`，口径元数据见 `marketEnvelope.ts`。
 */
export function fetchDistribution(): Promise<MarketListEnvelope<DistributionItem>> {
  return apiGet<BackendMarketListOut<DistributionItem>>("/api/v1/market/distribution").then(mapMarketList);
}

/** 板块涨跌（CSRC 口径）。同 {@link fetchDistribution}：返回 `{items, 口径元数据}` 信封。 */
export function fetchSectors(): Promise<MarketListEnvelope<SectorSummary>> {
  return apiGet<BackendMarketListOut<SectorSummary>>("/api/v1/market/sectors").then(mapMarketList);
}

// ---------------------------------------------------------------------------
// 申万一级行业行情聚合（Task 3.1 / 3.2）
// ---------------------------------------------------------------------------

export interface SwPerformanceItem {
  code: string;
  name: string;
  /** 当日有行情（pct_chg 非空）的成员数。 */
  member_count: number;
  avg_pct_chg: number;
  /**
   * 成交额，TuShare 原生 千元（消费端 ×1000 → 元，与 rankings/StockEnrichedOut 同口径）。
   * 可空：`sum(amount)` 在该组报价成员均无成交额时为 NULL，后端刻意保留 `float | None`
   * 以避免匿名端点触发 Pydantic 500（见 backend/app/schemas/sw_performance.py）。UI 渲染 `--`。
   */
  total_amount: number | null;
  up_count: number;
  down_count: number;
}

export interface SwPerformanceResponse {
  as_of: string;
  /** 判据日完整性口径（后端 Task 2 起返回）；Phase 1 徽标消费。 */
  as_of_quality: AsOfQuality;
  as_of_reason: string | null;
  items: SwPerformanceItem[];
}

export function fetchSwPerformance(): Promise<SwPerformanceResponse> {
  return apiGet<SwPerformanceResponse>("/api/v1/market/sw-industry/performance?limit=31");
}

/** 板块资金流（近似口径）。同 {@link fetchDistribution}：返回 `{items, 口径元数据}` 信封。 */
export function fetchCapitalFlow(): Promise<MarketListEnvelope<CapitalFlowItem>> {
  return apiGet<BackendMarketListOut<CapitalFlowItem>>("/api/v1/market/capital-flow").then(mapMarketList);
}

/** 热门板块。同 {@link fetchDistribution}：返回 `{items, 口径元数据}` 信封。 */
export function fetchHotBoards(category: HotBoardCategory): Promise<MarketListEnvelope<HotBoardItem>> {
  return apiGet<BackendMarketListOut<HotBoardItem>>("/api/v1/market/hot-boards", { category }).then(mapMarketList);
}

export function fetchSseIntraday(code: string, date?: string): Promise<SseIntradayResponse> {
  return apiGet<SseIntradayResponse>(`/api/v1/market/sse-snapshots/${code}/intraday`, date ? { date } : undefined);
}

export async function fetchIndexKline(tsCode: string, days: number): Promise<KlineResult> {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - days);
  const startDate = start.toISOString().slice(0, 10);
  const endDate = end.toISOString().slice(0, 10);

  const resp = await apiGet<IndexKlineResponse>(
    `/api/v1/market/indices/${tsCode}/kline`,
    { start: startDate, end: endDate },
  );

  const points: KLinePoint[] = resp.data.map((item) => {
    const open = item.open ?? item.close;
    const high = item.high ?? Math.max(open, item.close);
    const low = item.low ?? Math.min(open, item.close);
    return {
      date: item.trade_date,
      open,
      close: item.close,
      high,
      low,
      volume: item.volume ?? 0,
      amount: item.amount ?? undefined,
    };
  });
  // 指数无复权概念，恒为可用
  return { points, adjustAvailable: true };
}
