import { apiGet } from "./client";

// ---------------------------------------------------------------------------
// 监管异动雷达（`GET /api/v1/market/regulatory-anomalies`）。
//
// 后端 payload 为 snake_case；映射只在本文件一处做，消费端一律 camelCase。
// 口径（阈值/基准指数/已知失效场景）由后端 `thresholds` / `caveats` 下发，
// 前端**不复制**一份会漂移的常量表。
// ---------------------------------------------------------------------------

export type RegulatoryDirection = "up" | "down";
export type RegulatoryStatus = "triggered" | "near" | "watch";

export interface RegulatoryNextTrigger {
  price: number;
  neededPct: number;
  partialPct: number;
  limitPct: number;
  feasible: boolean;
}

export interface RegulatoryItem {
  symbol: string;
  name: string;
  board: string;
  isSt: boolean;
  direction: RegulatoryDirection;
  status: RegulatoryStatus;
  close: number;
  dayPct: number | null;
  thresholdPct: number;
  dev3Pct: number | null;
  dev10Pct: number | null;
  dev30Pct: number | null;
  events10d: number;
  severeFlags: string[];
  streak: number | null;
  daysSpan: number | null;
  boardsInWindow: number | null;
  limitUp: boolean;
  limitDown: boolean;
  benchmarkCode: string;
  benchmarkName: string;
  nextTrigger: RegulatoryNextTrigger | null;
}

export interface RegulatoryHistoryItem {
  symbol: string;
  name: string;
  board: string;
  direction: RegulatoryDirection;
  dev3Pct: number;
}

export interface RegulatoryHistoryDay {
  tradeDate: string;
  items: RegulatoryHistoryItem[];
}

export interface RegulatoryTurnoverOnly {
  symbol: string;
  name: string;
  board: string;
  isSt: boolean;
  close: number;
  dayPct: number | null;
  turnover3dPct: number;
  turnover3dAvgPct: number;
  turnoverPrev5AvgPct: number;
  turnoverRatio: number;
}

export interface RegulatorySummary {
  upTriggered: number;
  upNear: number;
  downTriggered: number;
  downNear: number;
  severe: number;
  limitUp: number;
  limitDown: number;
}

export interface RegulatoryThreshold {
  board: string;
  exchange: string;
  thresholdPct: number;
  stThresholdPct: number | null;
  priceLimitPct: number;
  stPriceLimitPct: number | null;
  benchmarkCode: string;
  benchmarkName: string;
}

export interface RegulatoryRadar {
  asOf: string | null;
  asOfQuality: "complete" | "partial" | "fallback" | "requested";
  staleDays: number | null;
  degradedReason: string | null;
  summary: RegulatorySummary;
  items: RegulatoryItem[];
  history: RegulatoryHistoryDay[];
  turnoverOnly: RegulatoryTurnoverOnly[];
  turnoverAvailable: boolean;
  turnoverAsOf: string | null;
  thresholds: RegulatoryThreshold[];
  caveats: string[];
}

interface BackendNextTrigger {
  price: number;
  needed_pct: number;
  partial_pct: number;
  limit_pct: number;
  feasible: boolean;
}

interface BackendItem {
  symbol: string;
  name: string;
  board: string;
  is_st: boolean;
  direction: RegulatoryDirection;
  status: RegulatoryStatus;
  close: number;
  day_pct: number | null;
  threshold_pct: number;
  dev3_pct: number | null;
  dev10_pct: number | null;
  dev30_pct: number | null;
  events_10d: number;
  severe_flags: string[];
  streak: number | null;
  days_span: number | null;
  boards_in_window: number | null;
  limit_up: boolean;
  limit_down: boolean;
  benchmark_code: string;
  benchmark_name: string;
  next_trigger: BackendNextTrigger | null;
}

interface BackendHistoryDay {
  trade_date: string;
  items: Array<{
    symbol: string;
    name: string;
    board: string;
    direction: RegulatoryDirection;
    dev3_pct: number;
  }>;
}

interface BackendRadar {
  as_of: string | null;
  as_of_quality: RegulatoryRadar["asOfQuality"];
  stale_days: number | null;
  degraded_reason: string | null;
  summary: {
    up_triggered: number;
    up_near: number;
    down_triggered: number;
    down_near: number;
    severe: number;
    limit_up: number;
    limit_down: number;
  };
  items: BackendItem[];
  history: BackendHistoryDay[];
  turnover_only: Array<{
    symbol: string;
    name: string;
    board: string;
    is_st: boolean;
    close: number;
    day_pct: number | null;
    turnover_3d_pct: number;
    turnover_3d_avg_pct: number;
    turnover_prev5_avg_pct: number;
    turnover_ratio: number;
  }>;
  turnover_available: boolean;
  turnover_as_of: string | null;
  thresholds: Array<{
    board: string;
    exchange: string;
    threshold_pct: number;
    st_threshold_pct: number | null;
    price_limit_pct: number;
    st_price_limit_pct: number | null;
    benchmark_code: string;
    benchmark_name: string;
  }>;
  caveats: string[];
}

export async function fetchRegulatoryRadar(params: {
  date?: string;
  historyDays?: number;
}): Promise<RegulatoryRadar> {
  const raw = await apiGet<BackendRadar>("/api/v1/market/regulatory-anomalies", {
    date: params.date,
    history_days: params.historyDays,
  });
  return {
    asOf: raw.as_of,
    asOfQuality: raw.as_of_quality,
    staleDays: raw.stale_days ?? null,
    degradedReason: raw.degraded_reason,
    summary: {
      upTriggered: raw.summary.up_triggered,
      upNear: raw.summary.up_near,
      downTriggered: raw.summary.down_triggered,
      downNear: raw.summary.down_near,
      severe: raw.summary.severe,
      limitUp: raw.summary.limit_up,
      limitDown: raw.summary.limit_down,
    },
    items: raw.items.map((item) => ({
      symbol: item.symbol,
      name: item.name,
      board: item.board,
      isSt: item.is_st,
      direction: item.direction,
      status: item.status,
      close: item.close,
      dayPct: item.day_pct,
      thresholdPct: item.threshold_pct,
      dev3Pct: item.dev3_pct,
      dev10Pct: item.dev10_pct,
      dev30Pct: item.dev30_pct,
      events10d: item.events_10d,
      severeFlags: item.severe_flags,
      streak: item.streak,
      daysSpan: item.days_span,
      boardsInWindow: item.boards_in_window,
      limitUp: item.limit_up,
      limitDown: item.limit_down,
      benchmarkCode: item.benchmark_code,
      benchmarkName: item.benchmark_name,
      nextTrigger: item.next_trigger
        ? {
            price: item.next_trigger.price,
            neededPct: item.next_trigger.needed_pct,
            partialPct: item.next_trigger.partial_pct,
            limitPct: item.next_trigger.limit_pct,
            feasible: item.next_trigger.feasible,
          }
        : null,
    })),
    history: raw.history.map((day) => ({
      tradeDate: day.trade_date,
      items: day.items.map((item) => ({
        symbol: item.symbol,
        name: item.name,
        board: item.board,
        direction: item.direction,
        dev3Pct: item.dev3_pct,
      })),
    })),
    turnoverOnly: (raw.turnover_only ?? []).map((item) => ({
      symbol: item.symbol,
      name: item.name,
      board: item.board,
      isSt: item.is_st,
      close: item.close,
      dayPct: item.day_pct,
      turnover3dPct: item.turnover_3d_pct,
      turnover3dAvgPct: item.turnover_3d_avg_pct,
      turnoverPrev5AvgPct: item.turnover_prev5_avg_pct,
      turnoverRatio: item.turnover_ratio,
    })),
    turnoverAvailable: raw.turnover_available ?? false,
    turnoverAsOf: raw.turnover_as_of ?? null,
    thresholds: raw.thresholds.map((t) => ({
      board: t.board,
      exchange: t.exchange,
      thresholdPct: t.threshold_pct,
      stThresholdPct: t.st_threshold_pct,
      priceLimitPct: t.price_limit_pct,
      stPriceLimitPct: t.st_price_limit_pct,
      benchmarkCode: t.benchmark_code,
      benchmarkName: t.benchmark_name,
    })),
    caveats: raw.caveats,
  };
}
