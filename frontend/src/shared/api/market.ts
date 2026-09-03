import { apiGet } from "./client";
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

export function fetchDistribution(): Promise<DistributionItem[]> {
  return apiGet<DistributionItem[]>("/api/v1/market/distribution");
}

export function fetchSectors(): Promise<SectorSummary[]> {
  return apiGet<SectorSummary[]>("/api/v1/market/sectors");
}

export function fetchCapitalFlow(): Promise<CapitalFlowItem[]> {
  return apiGet<CapitalFlowItem[]>("/api/v1/market/capital-flow");
}

export function fetchHotBoards(category: HotBoardCategory): Promise<HotBoardItem[]> {
  return apiGet<HotBoardItem[]>("/api/v1/market/hot-boards", { category });
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
