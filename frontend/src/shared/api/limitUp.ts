import { apiGet } from "./client";
import type { AsOfQuality } from "./marketEnvelope";

// ---------------------------------------------------------------------------
// 连板梯队与市场情绪（Task 4/5/6 的 4 个端点 + Task 12 的盘中分时序列端点）。
// 后端 payload 为 snake_case；映射只在本文件一处做，消费端一律 camelCase。
//
// 双口径（Task 11/13）：三个涨停端点接受 `mode=close|intraday`。`close`（默认）是
// 本地权威口径、URL 与历史缓存字节级一致（**不**带 mode 参数）；`intraday` 是今日
// 东财盘中口径，URL 多一个 `mode=intraday`，与 close 各自成缓存条目（消费节奏不同）。
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
  /** 盘中口径标注：`盘中 HH:MM`；东财回落时 `盘中不可用，已回落收盘`；收盘恒 null。 */
  as_of_label?: string | null;
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
  /** 同 `BackendLadder.as_of_label`（三张卡同源同字段）。 */
  as_of_label?: string | null;
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
  /** 同 `BackendLadder.as_of_label`（三张卡同源同字段）。 */
  as_of_label?: string | null;
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

/** 盘中分时点（`SentimentIntradayPointOut` 的 1:1 对齐），端点按 captured_at 升序返回。 */
interface BackendSentimentIntradayPoint {
  id: number;
  trade_date: string;
  captured_at: string;
  zt_count: number;
  dt_count: number;
  zb_count: number;
  max_streak: number;
}

// ---- 前端 camelCase 类型（消费端契约） ----

/**
 * 情绪口径：
 * - `close`（默认）：本地权威口径，可回放、写入 `market_sentiment_daily`；
 * - `intraday`：今日东财盘中口径，仅今日有效，**不**写收盘权威序列。
 *
 * 两口径同端点同 schema，但消费节奏不同 → query key 必须带上本值（见 pages/market）。
 */
export type SentimentMode = "close" | "intraday";

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
  /** 盘中口径标注（收盘为 null）；原样透传后端串，前端不美化、不隐藏回落事实。 */
  asOfLabel: string | null;
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
  /** 见 `LimitUpLadder.asOfLabel`（三张卡同源同字段）。 */
  asOfLabel: string | null;
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
  /** 见 `LimitUpLadder.asOfLabel`（三张卡同源同字段）。 */
  asOfLabel: string | null;
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

/** 盘中分时点（前端契约）：抓取时刻 + 涨停/跌停/炸板/最高板计数。 */
export interface SentimentIntradayPoint {
  id: number;
  tradeDate: string;
  capturedAt: string;
  ztCount: number;
  dtCount: number;
  zbCount: number;
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

const mapIntradayPoint = (p: BackendSentimentIntradayPoint): SentimentIntradayPoint => ({
  id: p.id,
  tradeDate: p.trade_date,
  capturedAt: p.captured_at,
  ztCount: p.zt_count,
  dtCount: p.dt_count,
  zbCount: p.zb_count,
  maxStreak: p.max_streak,
});

/**
 * `mode` 只在盘中时才写进 query——收盘路径的 URL 必须与引入双口径前**字节级一致**
 * （否则会撞碎既有的 URL 级缓存与 e2e 里对 limit-up-ladder 端点的路由断言）。
 */
const modeParam = (mode: SentimentMode): SentimentMode | undefined =>
  mode === "close" ? undefined : mode;

// ---- 端点 ----

export function fetchLimitUpLadder(
  date?: string,
  lookback = 10,
  mode: SentimentMode = "close",
): Promise<LimitUpLadder> {
  return apiGet<BackendLadder>("/api/v1/market/limit-up-ladder", {
    date,
    lookback,
    mode: modeParam(mode),
  }).then((b) => ({
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
    asOfLabel: b.as_of_label ?? null,
  }));
}

export function fetchSectorLimitUp(
  date?: string,
  swL1?: string,
  mode: SentimentMode = "close",
): Promise<SectorLimitUp> {
  return apiGet<BackendSectorLimitUp>("/api/v1/market/sector-limit-up", {
    date,
    sw_l1: swL1,
    mode: modeParam(mode),
  }).then((b) => ({
    asOf: b.as_of,
    asOfQuality: b.as_of_quality ?? "partial",
    source: b.source,
    degradedReason: b.degraded_reason,
    unclassifiedCount: b.unclassified_count,
    items: b.items.map(mapSectorItem),
    asOfLabel: b.as_of_label ?? null,
  }));
}

export function fetchYesterdayLimitUp(
  date?: string,
  mode: SentimentMode = "close",
): Promise<YesterdayLimitUp> {
  return apiGet<BackendYesterdayLimitUp>("/api/v1/market/yesterday-limit-up", {
    date,
    mode: modeParam(mode),
  }).then((b) => ({
    asOf: b.as_of,
    asOfPrev: b.as_of_prev,
    asOfQuality: b.as_of_quality ?? "partial",
    source: b.source,
    degradedReason: b.degraded_reason,
    kpis: b.kpis,
    items: b.items.map(mapYesterdayItem),
    asOfLabel: b.as_of_label ?? null,
  }));
}

export function fetchSentimentCalendar(days = 30): Promise<SentimentCalendarPoint[]> {
  return apiGet<BackendSentimentCalendarPoint[]>("/api/v1/market/sentiment/calendar", { days }).then(
    (points) => points.map(mapCalendarPoint),
  );
}

/**
 * 盘中分时点序列（升序）。`date` 缺省=后端按上海时区取今天——前端不自行算「今天」，
 * 避免浏览器本地时区把夜间的请求打成前一天。
 */
export function fetchSentimentIntraday(date?: string): Promise<SentimentIntradayPoint[]> {
  return apiGet<BackendSentimentIntradayPoint[]>("/api/v1/market/sentiment/intraday", { date }).then(
    (points) => points.map(mapIntradayPoint),
  );
}
