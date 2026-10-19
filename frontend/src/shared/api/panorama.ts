import { apiGet } from "./client";
import type { AsOfQuality } from "./marketEnvelope";

// ---------------------------------------------------------------------------
// A股全景（`GET /api/v1/market/panorama`）
//
// 读的是三张日表（广度/11 桶/行业），**不在前端现算历史**。响应自带
// as_of/expected_as_of/stale 与 flow_available_from：缺失/滞后必须在 UI 里
// 如实显示，不能把 4 天前的资金流读成今天。
// ---------------------------------------------------------------------------

export type StructureState = "broad_rally" | "structured" | "broad_decline" | "concentration";
export type StructureTone = "hot" | "neutral" | "cold" | "extreme";

interface BackendBreadthPoint {
  trade_date: string;
  up_count: number;
  down_count: number;
  flat_count: number;
  limit_up_count: number | null;
  limit_down_count: number | null;
  median_pct: number | null;
  mean_pct: number | null;
  amount: number | null;
  up_ratio: number | null;
}

interface BackendPanorama {
  as_of: string | null;
  expected_as_of: string | null;
  as_of_quality: AsOfQuality;
  stale: boolean | null;
  lag_days: number | null;
  window: { days: number; from: string | null; to: string | null; samples: number };
  breadth: {
    latest: BackendBreadthPoint | null;
    previous: BackendBreadthPoint | null;
    series: BackendBreadthPoint[];
  };
  buckets: Array<{ bucket: string; count: number }>;
  industries: Array<{
    industry_code: string;
    industry_name: string;
    pct_chg: number | null;
    up_count: number;
    down_count: number;
    flat_count: number;
    limit_up_count: number | null;
    amount: number | null;
    breadth: number | null;
    rank: number;
    rank_delta: number | null;
    attribution: string;
  }>;
  structure: {
    current: {
      trade_date: string;
      up_ratio: number | null;
      top3_share: number | null;
      hhi: number | null;
      dispersion: number | null;
      state: StructureState;
      label: string;
      tone: StructureTone;
    } | null;
    series: Array<{
      trade_date: string;
      up_ratio: number | null;
      top3_share: number | null;
      hhi: number | null;
      dispersion: number | null;
      state: StructureState;
      label: string;
      tone: StructureTone;
    }>;
  };
  flows: {
    as_of: string;
    available_from: string | null;
    top_in: Array<{
      board_code: string;
      board_name: string | null;
      pct_change: number | null;
      main_net_inflow: number | null;
      net_ratio: number | null;
    }>;
    top_out: Array<{
      board_code: string;
      board_name: string | null;
      pct_change: number | null;
      main_net_inflow: number | null;
      net_ratio: number | null;
    }>;
  } | null;
  freshness: {
    breadth_as_of: string | null;
    industry_as_of: string | null;
    flow_as_of: string | null;
    flow_available_from: string | null;
  };
}

export interface PanoramaBreadthPoint {
  tradeDate: string;
  upCount: number;
  downCount: number;
  flatCount: number;
  limitUpCount: number | null;
  limitDownCount: number | null;
  medianPct: number | null;
  meanPct: number | null;
  /** 千元（与 daily_quotes.amount 同单位） */
  amount: number | null;
  upRatio: number | null;
}

export interface PanoramaIndustry {
  code: string;
  name: string;
  pctChg: number | null;
  upCount: number;
  downCount: number;
  flatCount: number;
  limitUpCount: number | null;
  amount: number | null;
  breadth: number | null;
  rank: number;
  rankDelta: number | null;
  attribution: string;
}

export interface PanoramaStructurePoint {
  tradeDate: string;
  upRatio: number | null;
  top3Share: number | null;
  hhi: number | null;
  dispersion: number | null;
  state: StructureState;
  label: string;
  tone: StructureTone;
}

export interface PanoramaFlowItem {
  code: string;
  name: string;
  pctChange: number | null;
  /** 元 */
  mainNetInflow: number | null;
  netRatio: number | null;
}

export interface MarketPanorama {
  asOf: string | null;
  expectedAsOf: string | null;
  asOfQuality: AsOfQuality;
  stale: boolean | null;
  lagDays: number | null;
  window: { days: number; from: string | null; to: string | null; samples: number };
  breadth: {
    latest: PanoramaBreadthPoint | null;
    previous: PanoramaBreadthPoint | null;
    series: PanoramaBreadthPoint[];
  };
  buckets: Array<{ bucket: string; count: number }>;
  industries: PanoramaIndustry[];
  structure: {
    current: PanoramaStructurePoint | null;
    series: PanoramaStructurePoint[];
  };
  flows: {
    asOf: string;
    availableFrom: string | null;
    topIn: PanoramaFlowItem[];
    topOut: PanoramaFlowItem[];
  } | null;
  freshness: {
    breadthAsOf: string | null;
    industryAsOf: string | null;
    flowAsOf: string | null;
    flowAvailableFrom: string | null;
  };
}

const mapBreadth = (p: BackendBreadthPoint): PanoramaBreadthPoint => ({
  tradeDate: p.trade_date,
  upCount: p.up_count,
  downCount: p.down_count,
  flatCount: p.flat_count,
  limitUpCount: p.limit_up_count,
  limitDownCount: p.limit_down_count,
  medianPct: p.median_pct,
  meanPct: p.mean_pct,
  amount: p.amount,
  upRatio: p.up_ratio,
});

const mapStructure = (p: BackendPanorama["structure"]["series"][number]): PanoramaStructurePoint => ({
  tradeDate: p.trade_date,
  upRatio: p.up_ratio,
  top3Share: p.top3_share,
  hhi: p.hhi,
  dispersion: p.dispersion,
  state: p.state,
  label: p.label,
  tone: p.tone,
});

const mapFlow = (p: NonNullable<BackendPanorama["flows"]>["top_in"][number]): PanoramaFlowItem => ({
  code: p.board_code,
  name: p.board_name ?? p.board_code,
  pctChange: p.pct_change,
  mainNetInflow: p.main_net_inflow,
  netRatio: p.net_ratio,
});

export function fetchMarketPanorama(days = 60): Promise<MarketPanorama> {
  return apiGet<BackendPanorama>("/api/v1/market/panorama", { days }).then((p) => ({
    asOf: p.as_of,
    expectedAsOf: p.expected_as_of,
    asOfQuality: p.as_of_quality,
    stale: p.stale,
    lagDays: p.lag_days,
    window: p.window,
    breadth: {
      latest: p.breadth.latest ? mapBreadth(p.breadth.latest) : null,
      previous: p.breadth.previous ? mapBreadth(p.breadth.previous) : null,
      series: p.breadth.series.map(mapBreadth),
    },
    buckets: p.buckets,
    industries: p.industries.map((i) => ({
      code: i.industry_code,
      name: i.industry_name,
      pctChg: i.pct_chg,
      upCount: i.up_count,
      downCount: i.down_count,
      flatCount: i.flat_count,
      limitUpCount: i.limit_up_count,
      amount: i.amount,
      breadth: i.breadth,
      rank: i.rank,
      rankDelta: i.rank_delta,
      attribution: i.attribution,
    })),
    structure: {
      current: p.structure.current ? mapStructure(p.structure.current) : null,
      series: p.structure.series.map(mapStructure),
    },
    flows: p.flows
      ? {
          asOf: p.flows.as_of,
          availableFrom: p.flows.available_from,
          topIn: p.flows.top_in.map(mapFlow),
          topOut: p.flows.top_out.map(mapFlow),
        }
      : null,
    freshness: {
      breadthAsOf: p.freshness.breadth_as_of,
      industryAsOf: p.freshness.industry_as_of,
      flowAsOf: p.freshness.flow_as_of,
      flowAvailableFrom: p.freshness.flow_available_from,
    },
  }));
}

/** 千元 → 亿元（1 亿 = 100000 千元） */
export function formatAmountQianToYi(qian: number | null): string {
  if (qian == null) return "—";
  const yi = qian / 100_000;
  return yi >= 10_000 ? `${(yi / 10_000).toFixed(2)}万亿` : `${yi.toFixed(0)}亿`;
}

/** 元 → 亿元（资金流） */
export function formatYuanToYi(yuan: number | null): string {
  if (yuan == null) return "—";
  return `${(yuan / 100_000_000).toFixed(2)}亿`;
}
