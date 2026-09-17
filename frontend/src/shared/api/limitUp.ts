import { apiGet } from "./client";
import type { AsOfQuality } from "./marketEnvelope";

// ---------------------------------------------------------------------------
// 连板梯队与市场情绪（Task 4/5/6 的 4 个端点）。
// 后端 payload 为 snake_case；映射只在本文件一处做，消费端一律 camelCase。
// ---------------------------------------------------------------------------

// ---- 后端原始 payload（snake_case，只在 mapper 里解包） ----

interface BackendLadderStock {
  symbol: string;
  name: string;
  streak: number;
  days_span: number;
  boards_in_window: number;
  missing_days: number;
  sw_l1_name: string | null;
  sw_l3_name: string | null;
  seal_time: string | null;
  seal_fund: number | null;
  break_count: number | null;
}

interface BackendKpis {
  zt_count: number;
  dt_count: number;
  zb_count: number;
  broken_rate: number | null;
  yzt_avg_pct: number | null;
  yzt_avg_open_premium: number | null;
  yzt_n: number;
  promo_1to2: number | null;
  promo_1to2_n: number;
  promo_1to2_noisy: boolean;
  promo_2to3: number | null;
  promo_2to3_n: number;
  promo_2to3_noisy: boolean;
  max_streak: number;
}

interface BackendLadder {
  as_of: string | null;
  as_of_prev: string | null;
  as_of_quality?: AsOfQuality;
  source: "local_calc";
  limits_present: boolean;
  is_partial: boolean;
  sw_coverage: number | null;
  degraded_reason: string | null;
  kpis: BackendKpis | null;
  echelons: Array<{ streak: number; label: string; stocks: BackendLadderStock[] }>;
}

interface BackendSectorLimitUpItem {
  l3_code: string;
  l3_name: string | null;
  l1_code: string | null;
  l1_name: string | null;
  max_streak: number;
  leader_symbol: string;
  leader_name: string | null;
  leader_streak: number;
  zt_count: number;
}

interface BackendSectorLimitUp {
  as_of: string | null;
  as_of_quality?: AsOfQuality;
  source: "local_calc";
  degraded_reason: string | null;
  unclassified_count: number;
  items: BackendSectorLimitUpItem[];
}

interface BackendYesterdayLimitUpItem {
  symbol: string;
  name: string;
  prev_streak: number;
  today_pct: number | null;
  today_open_premium: number | null;
  today_streak: number | null;
  is_lu: boolean;
  touched: boolean;
  broken: boolean;
  suspended: boolean;
  missing_days: number;
  sw_l3_name: string | null;
}

interface BackendYesterdayLimitUp {
  as_of: string | null;
  as_of_prev: string | null;
  as_of_quality?: AsOfQuality;
  source: "local_calc";
  degraded_reason: string | null;
  kpis: Record<string, unknown>;
  items: BackendYesterdayLimitUpItem[];
}

interface BackendSentimentCalendarPoint {
  trade_date: string;
  zt_count: number;
  dt_count: number;
  zb_count: number;
  broken_rate: number | null;
  yzt_avg_pct: number | null;
  promo_1to2: number | null;
  promo_2to3: number | null;
  max_streak: number;
}

// ---- 前端 camelCase 类型（消费端契约） ----

export interface LadderStock {
  symbol: string;
  name: string;
  streak: number;
  daysSpan: number;
  boardsInWindow: number;
  missingDays: number;
  swL1Name: string | null;
  swL3Name: string | null;
  sealTime: string | null;
  sealFund: number | null;
  breakCount: number | null;
}

export interface SentimentKpis {
  ztCount: number;
  dtCount: number;
  zbCount: number;
  brokenRate: number | null;
  yztAvgPct: number | null;
  yztAvgOpenPremium: number | null;
  yztN: number;
  promo1to2: number | null;
  promo1to2N: number;
  promo1to2Noisy: boolean;
  promo2to3: number | null;
  promo2to3N: number;
  promo2to3Noisy: boolean;
  maxStreak: number;
}

export interface LimitUpLadder {
  asOf: string | null;
  asOfPrev: string | null;
  /** 判据日完整性口径（后端 Task 2 起返回，默认 partial）。 */
  asOfQuality: AsOfQuality;
  source: "local_calc";
  limitsPresent: boolean;
  isPartial: boolean;
  swCoverage: number | null;
  degradedReason: string | null;
  kpis: SentimentKpis | null;
  echelons: Array<{ streak: number; label: string; stocks: LadderStock[] }>;
}

export interface SectorLimitUpItem {
  l3Code: string;
  l3Name: string | null;
  l1Code: string | null;
  l1Name: string | null;
  maxStreak: number;
  leaderSymbol: string;
  leaderName: string | null;
  leaderStreak: number;
  ztCount: number;
}

export interface SectorLimitUp {
  asOf: string | null;
  /** 判据日完整性口径（后端 Task 2 起返回，默认 partial）。 */
  asOfQuality: AsOfQuality;
  source: "local_calc";
  degradedReason: string | null;
  unclassifiedCount: number;
  items: SectorLimitUpItem[];
}

export interface YesterdayLimitUpItem {
  symbol: string;
  name: string;
  prevStreak: number;
  todayPct: number | null;
  todayOpenPremium: number | null;
  todayStreak: number | null;
  isLu: boolean;
  touched: boolean;
  broken: boolean;
  suspended: boolean;
  missingDays: number;
  swL3Name: string | null;
}

export interface YesterdayLimitUp {
  asOf: string | null;
  asOfPrev: string | null;
  /** 判据日完整性口径（后端 Task 2 起返回，默认 partial）。 */
  asOfQuality: AsOfQuality;
  source: "local_calc";
  degradedReason: string | null;
  kpis: Record<string, unknown>;
  items: YesterdayLimitUpItem[];
}

export interface SentimentCalendarPoint {
  tradeDate: string;
  ztCount: number;
  dtCount: number;
  zbCount: number;
  brokenRate: number | null;
  yztAvgPct: number | null;
  promo1to2: number | null;
  promo2to3: number | null;
  maxStreak: number;
}

// ---- mapper（字段一一对应，不做任何语义重算） ----

const mapStock = (s: BackendLadderStock): LadderStock => ({
  symbol: s.symbol,
  name: s.name,
  streak: s.streak,
  daysSpan: s.days_span,
  boardsInWindow: s.boards_in_window,
  missingDays: s.missing_days,
  swL1Name: s.sw_l1_name,
  swL3Name: s.sw_l3_name,
  sealTime: s.seal_time,
  sealFund: s.seal_fund,
  breakCount: s.break_count,
});

const mapKpis = (k: BackendKpis): SentimentKpis => ({
  ztCount: k.zt_count,
  dtCount: k.dt_count,
  zbCount: k.zb_count,
  brokenRate: k.broken_rate,
  yztAvgPct: k.yzt_avg_pct,
  yztAvgOpenPremium: k.yzt_avg_open_premium,
  yztN: k.yzt_n,
  promo1to2: k.promo_1to2,
  promo1to2N: k.promo_1to2_n,
  promo1to2Noisy: k.promo_1to2_noisy,
  promo2to3: k.promo_2to3,
  promo2to3N: k.promo_2to3_n,
  promo2to3Noisy: k.promo_2to3_noisy,
  maxStreak: k.max_streak,
});

const mapSectorItem = (s: BackendSectorLimitUpItem): SectorLimitUpItem => ({
  l3Code: s.l3_code,
  l3Name: s.l3_name,
  l1Code: s.l1_code,
  l1Name: s.l1_name,
  maxStreak: s.max_streak,
  leaderSymbol: s.leader_symbol,
  leaderName: s.leader_name,
  leaderStreak: s.leader_streak,
  ztCount: s.zt_count,
});

const mapYesterdayItem = (s: BackendYesterdayLimitUpItem): YesterdayLimitUpItem => ({
  symbol: s.symbol,
  name: s.name,
  prevStreak: s.prev_streak,
  todayPct: s.today_pct,
  todayOpenPremium: s.today_open_premium,
  todayStreak: s.today_streak,
  isLu: s.is_lu,
  touched: s.touched,
  broken: s.broken,
  suspended: s.suspended,
  missingDays: s.missing_days,
  swL3Name: s.sw_l3_name,
});

const mapCalendarPoint = (p: BackendSentimentCalendarPoint): SentimentCalendarPoint => ({
  tradeDate: p.trade_date,
  ztCount: p.zt_count,
  dtCount: p.dt_count,
  zbCount: p.zb_count,
  brokenRate: p.broken_rate,
  yztAvgPct: p.yzt_avg_pct,
  promo1to2: p.promo_1to2,
  promo2to3: p.promo_2to3,
  maxStreak: p.max_streak,
});

// ---- 端点 ----

export function fetchLimitUpLadder(date?: string, lookback = 10): Promise<LimitUpLadder> {
  return apiGet<BackendLadder>("/api/v1/market/limit-up-ladder", { date, lookback }).then((b) => ({
    asOf: b.as_of,
    asOfPrev: b.as_of_prev,
    // 缺省回合 `partial`：口径未知时不得谎报「收盘」
    asOfQuality: b.as_of_quality ?? "partial",
    source: b.source,
    limitsPresent: b.limits_present,
    isPartial: b.is_partial,
    swCoverage: b.sw_coverage,
    degradedReason: b.degraded_reason,
    kpis: b.kpis ? mapKpis(b.kpis) : null,
    echelons: b.echelons.map((e) => ({ streak: e.streak, label: e.label, stocks: e.stocks.map(mapStock) })),
  }));
}

export function fetchSectorLimitUp(date?: string, swL1?: string): Promise<SectorLimitUp> {
  return apiGet<BackendSectorLimitUp>("/api/v1/market/sector-limit-up", { date, sw_l1: swL1 }).then((b) => ({
    asOf: b.as_of,
    asOfQuality: b.as_of_quality ?? "partial",
    source: b.source,
    degradedReason: b.degraded_reason,
    unclassifiedCount: b.unclassified_count,
    items: b.items.map(mapSectorItem),
  }));
}

export function fetchYesterdayLimitUp(date?: string): Promise<YesterdayLimitUp> {
  return apiGet<BackendYesterdayLimitUp>("/api/v1/market/yesterday-limit-up", { date }).then((b) => ({
    asOf: b.as_of,
    asOfPrev: b.as_of_prev,
    asOfQuality: b.as_of_quality ?? "partial",
    source: b.source,
    degradedReason: b.degraded_reason,
    kpis: b.kpis,
    items: b.items.map(mapYesterdayItem),
  }));
}

export function fetchSentimentCalendar(days = 30): Promise<SentimentCalendarPoint[]> {
  return apiGet<BackendSentimentCalendarPoint[]>("/api/v1/market/sentiment/calendar", { days }).then(
    (points) => points.map(mapCalendarPoint),
  );
}
