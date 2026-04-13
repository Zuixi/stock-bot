import { apiGet } from "./client";
import type { MarketIndex, SectorSummary } from "@/shared/types";

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
