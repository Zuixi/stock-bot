import { apiGet } from "./client";

/**
 * `/api/v1/market/heatmap/treemap` 的前端形状（S1.5）。
 *
 * 与后端 `app/schemas/heatmap.py` 同键（snake_case 直传，与 market.ts 的榜单信封
 * 同一约定：解包只在 mapper，不做二次命名漂移）。单位语义由后端显式下发
 * （`amount_unit` / `mv_unit`），面积切换的 UI 标注直接消费——前端不猜单位。
 */
export interface HeatmapStockItem {
  symbol: string;
  name: string | null;
  /** 涨跌幅（%），恒非空（后端 SQL 只取当日真有涨跌的行）。 */
  pct_chg: number;
  /** 成交额（千元，TuShare 原生）；缺数 null。 */
  amount: number | null;
  /** 总市值（万元，daily_basic 当日优先、缺日回看）；缺数 null。 */
  total_mv: number | null;
  /** 申万一级归属码（每股取码最小的一个）；无归属 null → 「未分类」组。 */
  sw_l1_code: string | null;
  /** 主概念板块码（每股取码最小的一个）；无归属 null → 「未分类」组。 */
  concept_code: string | null;
}

export type AsOfQuality = "complete" | "partial" | "fallback";

export interface HeatmapTreemapEnvelope {
  as_of: string | null;
  as_of_quality: AsOfQuality;
  count: number;
  amount_unit: "千元";
  mv_unit: "万元";
  items: HeatmapStockItem[];
}

/** `{code,name}` 名录项（申万一级 / 概念板块共用）。 */
export interface HeatmapNameRef {
  code: string;
  name: string;
}

export function fetchHeatmapTreemap(): Promise<HeatmapTreemapEnvelope> {
  return apiGet<HeatmapTreemapEnvelope>("/api/v1/market/heatmap/treemap");
}

export function fetchHeatmapSwL1(): Promise<HeatmapNameRef[]> {
  return apiGet<HeatmapNameRef[]>("/api/v1/market/heatmap/sw-l1");
}

export function fetchHeatmapConceptBoards(): Promise<HeatmapNameRef[]> {
  return apiGet<{ boardCode: string; name: string }[]>(
    "/api/v1/market/heatmap/concept-boards"
  ).then((rows) => rows.map((r) => ({ code: r.boardCode, name: r.name })));
}
