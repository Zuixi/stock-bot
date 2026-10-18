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
  /** 收盘口径为 SW L3 码；**盘中口径为 null**（东财体系），此时板块名看 `board_name`。 */
  l3_code: string | null;
  l3_name: string | null;
  l1_code: string | null;
  l1_name: string | null;
  /** 仅盘中口径有值：东财板块名（前端据此切「盘中板块（东财口径）」）。 */
  board_name?: string | null;
  max_streak: number;
  leader_symbol: string;
  leader_name: string | null;
  leader_streak: number;
  zt_count: number;
}

interface BackendSectorLimitUp {
  as_of: string | null;
  as_of_quality?: AsOfQuality;
  source: "local_calc" | "eastmoney_intraday";
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
  yzt_avg_open_premium?: number | null;
  yzt_n?: number;
  promo_1to2: number | null;
  promo_2to3: number | null;
  max_streak: number;
}

interface BackendSentimentTrendPoint {
  trade_date: string;
  zt_count: number;
  dt_count: number;
  zb_count: number;
  /** 分母为 0 的日子为 NULL（**缺失 ≠ 0**），那些日子不进分位样本。 */
  broken_rate: number | null;
  yzt_avg_pct?: number | null;
  yzt_avg_open_premium?: number | null;
  yzt_n?: number;
  promo_1to2: number | null;
  promo_1to2_n?: number;
  promo_2to3?: number | null;
  promo_2to3_n?: number;
  max_streak: number;
}

/** `SentimentWindowOut`：`days` 是**请求**的交易日数，`samples` 是实际纳入行数。 */
interface BackendSentimentTrendWindow {
  days: number;
  from: string | null;
  to: string | null;
  samples: number;
}

/** `SentimentPercentileOut`：nearest-rank 分位梯，`sample_count` 是**该字段**的有效样本数。 */
interface BackendSentimentPercentile {
  field: string;
  sample_count: number;
  from: string | null;
  to: string | null;
  p0: number | null;
  p10: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p90: number | null;
  p100: number | null;
}

interface BackendSentimentStreak {
  zt_above_median_days?: number;
}

/** `SentimentTrendsOut`（Task 6a）：读时计算，`percentile=null` = 样本不足。 */
interface BackendSentimentTrends {
  as_of: string | null;
  as_of_quality?: AsOfQuality;
  window: BackendSentimentTrendWindow;
  series?: BackendSentimentTrendPoint[];
  percentile: Record<string, BackendSentimentPercentile> | null;
  streak?: BackendSentimentStreak;
  insufficient_history?: boolean;
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
  /** 收盘口径为 SW L3 码；**盘中口径为 null**（不能假定为非空字符串 —— 曾因此崩溃）。 */
  l3Code: string | null;
  l3Name: string | null;
  l1Code: string | null;
  l1Name: string | null;
  /** 仅盘中口径有值：东财板块名。 */
  boardName: string | null;
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
  /** 收盘 = local_calc（本地自算）；盘中 = eastmoney_intraday（东财涨停池）。 */
  source: "local_calc" | "eastmoney_intraday";
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
  yztAvgOpenPremium: number | null;
  yztN: number;
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

/** 允许做历史分位的指标（后端拍板 R6 只 3 个，**不得扩张**）。 */
export type SentimentPercentileField = "zt_count" | "broken_rate" | "promo_1to2";

/** 窗口内一个交易日的情绪行；`brokenRate`/`promo1to2` 为 `null` = 当日算不出该比值（**不是 0**）。 */
export interface SentimentTrendPoint {
  tradeDate: string;
  ztCount: number;
  dtCount: number;
  zbCount: number;
  brokenRate: number | null;
  yztAvgPct: number | null;
  yztAvgOpenPremium: number | null;
  yztN: number;
  promo1to2: number | null;
  promo1to2N: number;
  promo2to3: number | null;
  promo2to3N: number;
  maxStreak: number;
}

/** 窗口自描述：`days` = 请求交易日数，`samples` = 实际纳入行数（**不插值**）。 */
export interface SentimentTrendWindow {
  days: number;
  from: string | null;
  to: string | null;
  samples: number;
}

/** 单指标历史分位梯（nearest-rank）；`sampleCount` 是**该字段**的有效样本数。 */
export interface SentimentPercentile {
  field: string;
  sampleCount: number;
  from: string | null;
  to: string | null;
  p0: number | null;
  p10: number | null;
  p25: number | null;
  p50: number | null;
  p75: number | null;
  p90: number | null;
  p100: number | null;
}

/**
 * 情绪趋势 + 历史分位（`GET /market/sentiment/trends`）。
 *
 * `percentile=null` ⇔ `insufficientHistory`（有效样本 < 60，**不给假分位**）；
 * 非 null 时某指标仍可能缺席（该字段全部为 NULL → 无有效样本），消费端必须渲染 `--`。
 */
export interface SentimentTrends {
  asOf: string | null;
  asOfQuality: AsOfQuality;
  window: SentimentTrendWindow;
  series: SentimentTrendPoint[];
  percentile: Partial<Record<SentimentPercentileField, SentimentPercentile>> | null;
  /** 与后端同口径**非空**（`int = 0`）：0 是"没有一天高于中位数"的正经值，不是缺失。 */
  streak: { ztAboveMedianDays: number };
  insufficientHistory: boolean;
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
  boardName: s.board_name ?? null,
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
  yztAvgOpenPremium: p.yzt_avg_open_premium ?? null,
  yztN: p.yzt_n ?? 0,
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

const mapTrendPoint = (p: BackendSentimentTrendPoint): SentimentTrendPoint => ({
  tradeDate: p.trade_date,
  ztCount: p.zt_count,
  dtCount: p.dt_count,
  zbCount: p.zb_count,
  brokenRate: p.broken_rate,
  yztAvgPct: p.yzt_avg_pct ?? null,
  yztAvgOpenPremium: p.yzt_avg_open_premium ?? null,
  yztN: p.yzt_n ?? 0,
  promo1to2: p.promo_1to2,
  promo1to2N: p.promo_1to2_n ?? 0,
  promo2to3: p.promo_2to3 ?? null,
  promo2to3N: p.promo_2to3_n ?? 0,
  maxStreak: p.max_streak,
});

const mapPercentile = (p: BackendSentimentPercentile): SentimentPercentile => ({
  field: p.field,
  sampleCount: p.sample_count,
  from: p.from,
  to: p.to,
  p0: p.p0,
  p10: p.p10,
  p25: p.p25,
  p50: p.p50,
  p75: p.p75,
  p90: p.p90,
  p100: p.p100,
});

/** 后端 `PERCENTILE_FIELDS` 的 1:1 对齐（映射只在这里遍历，消费端不猜键名）。 */
const PERCENTILE_FIELDS: SentimentPercentileField[] = ["zt_count", "broken_rate", "promo_1to2"];

const mapSentimentTrends = (b: BackendSentimentTrends): SentimentTrends => {
  const percentile: Partial<Record<SentimentPercentileField, SentimentPercentile>> = {};
  for (const field of PERCENTILE_FIELDS) {
    const raw = b.percentile?.[field];
    if (raw) percentile[field] = mapPercentile(raw);
  }
  return {
    asOf: b.as_of,
    // 缺省回落 `partial`：口径未知时不得谎报「收盘」
    asOfQuality: b.as_of_quality ?? "partial",
    window: {
      days: b.window.days,
      from: b.window.from,
      to: b.window.to,
      samples: b.window.samples,
    },
    series: (b.series ?? []).map(mapTrendPoint),
    // 后端 `null` 原样保留（`{}` 会被消费端读成「有分位但全缺」）；null 是「样本不足」的唯一信号
    percentile: b.percentile ? percentile : null,
    streak: { ztAboveMedianDays: b.streak?.zt_above_median_days ?? 0 },
    // 缺省 `true`：拿不到「样本够不够」的证据时按样本不足处理，不给分位
    insufficientHistory: b.insufficient_history ?? true,
  };
};

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

/**
 * 情绪趋势 + 历史分位（读时计算，不物化）。`days` 是**请求**的交易日数，
 * 实际纳入行数看 `window.samples`（后端不插值，缺日就是没有那天）。
 */
export function fetchSentimentTrends(days = 250): Promise<SentimentTrends> {
  return apiGet<BackendSentimentTrends>("/api/v1/market/sentiment/trends", { days }).then(
    mapSentimentTrends,
  );
}

// ---------------------------------------------------------------------------
// 情绪指标详情（2026-10-18）：`GET /market/sentiment/metrics/{metric_key}`
//
// 温度计的 9 张卡各有一个"历史追踪"页。统计量/分位/分布**全部由后端算**——
// 前端只做量纲渲染（ratio ×100），不在浏览器里重算统计（与 /sentiment/trends 同一纪律）。
// ---------------------------------------------------------------------------

/** 9 张卡与后端 `SENTIMENT_METRIC_KEYS` 1:1（顺序即详情页切换器顺序）。 */
export const SENTIMENT_METRIC_KEYS = [
  "zt_count",
  "zb_count",
  "dt_count",
  "broken_rate",
  "yzt_avg_pct",
  "yzt_avg_open_premium",
  "promo_1to2",
  "promo_2to3",
  "max_streak",
] as const;

export type SentimentMetricKey = (typeof SENTIMENT_METRIC_KEYS)[number];

export function isSentimentMetricKey(value: string | undefined): value is SentimentMetricKey {
  return !!value && (SENTIMENT_METRIC_KEYS as readonly string[]).includes(value);
}

/** `count` 家 / `board` 板 / `ratio` 0–1（渲染 ×100%）/ `pct` 已是百分数。 */
export type SentimentMetricUnit = "count" | "board" | "ratio" | "pct";

/** 高值代表情绪更热（`high`）还是更冷（`low`）；由后端下发，前端不按 key 硬编码。 */
export type SentimentMetricHotterWhen = "high" | "low";

export type SentimentMetricGroup = "breadth" | "money" | "promotion" | "space";

interface BackendSentimentMetricMeta {
  key: string;
  label: string;
  unit: SentimentMetricUnit;
  group: SentimentMetricGroup;
  hotter_when: SentimentMetricHotterWhen;
  caliber: string;
}

interface BackendSentimentMetricPoint {
  trade_date: string;
  value: number | null;
  n?: number | null;
  low_sample?: boolean;
}

interface BackendSentimentMetricInterval {
  low: number;
  high: number;
  level: number;
  n: number;
  method: "wilson";
}

interface BackendSentimentMetricSampleGate {
  field: string | null;
  min_n: number;
  excluded_days: number;
}

interface BackendSentimentRegimeInput {
  key: string;
  label: string;
  value: number | null;
  percentile: number | null;
  unit: SentimentMetricUnit;
}

interface BackendSentimentRegimeCurrent {
  stage: string;
  label: string;
  tone: "cold" | "neutral" | "hot" | "extreme" | "muted";
  since: string;
  days: number;
  inputs: BackendSentimentRegimeInput[];
}

interface BackendSentimentRegimeSegment {
  stage: string;
  label: string;
  tone: "cold" | "neutral" | "hot" | "extreme" | "muted";
  from: string;
  to: string;
  days: number;
}

interface BackendSentimentRegime {
  version: string;
  current: BackendSentimentRegimeCurrent | null;
  segments: BackendSentimentRegimeSegment[];
}

interface BackendSentimentMetricStats {
  sample_count: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  p10: number | null;
  p25: number | null;
  p75: number | null;
  p90: number | null;
  std: number | null;
}

interface BackendSentimentMetricBin {
  from: number;
  to: number;
  count: number;
}

interface BackendSentimentMetricStreak {
  side: "above" | "below" | null;
  days: number;
  median: number | null;
}

interface BackendSentimentMetricPeer {
  key: string;
  label: string;
  unit: SentimentMetricUnit;
  latest_value: number | null;
  latest_date: string | null;
  low_sample?: boolean;
}

interface BackendSentimentMetricDetail {
  metric: BackendSentimentMetricMeta;
  as_of: string | null;
  as_of_quality?: AsOfQuality;
  window: BackendSentimentTrendWindow;
  latest: BackendSentimentMetricPoint | null;
  previous: BackendSentimentMetricPoint | null;
  delta: number | null;
  percentile_rank: number | null;
  stats: BackendSentimentMetricStats | null;
  distribution: BackendSentimentMetricBin[];
  streak: BackendSentimentMetricStreak;
  series: BackendSentimentMetricPoint[];
  peers: BackendSentimentMetricPeer[];
  insufficient_history: boolean;
  latest_interval?: BackendSentimentMetricInterval | null;
  sample_gate?: BackendSentimentMetricSampleGate | null;
  regime?: BackendSentimentRegime | null;
}

export interface SentimentMetricMeta {
  key: SentimentMetricKey;
  label: string;
  unit: SentimentMetricUnit;
  group: SentimentMetricGroup;
  hotterWhen: SentimentMetricHotterWhen;
  caliber: string;
}

export interface SentimentMetricPoint {
  tradeDate: string;
  value: number | null;
  /** 当日样本数；非样本型指标为 `null`（缺失 ≠ 0）。 */
  n: number | null;
  /** 样本不足（n < 5）：值仍展示，但不进统计量/分位/分布。 */
  lowSample: boolean;
}

export interface SentimentMetricInterval {
  low: number;
  high: number;
  level: number;
  n: number;
  method: "wilson";
}

export interface SentimentMetricSampleGate {
  field: string | null;
  minN: number;
  excludedDays: number;
}

export type SentimentRegimeTone = "cold" | "neutral" | "hot" | "extreme" | "muted";

export interface SentimentRegimeInput {
  key: string;
  label: string;
  value: number | null;
  percentile: number | null;
  unit: SentimentMetricUnit;
}

export interface SentimentRegimeCurrent {
  stage: string;
  label: string;
  tone: SentimentRegimeTone;
  since: string;
  days: number;
  inputs: SentimentRegimeInput[];
}

export interface SentimentRegimeSegment {
  stage: string;
  label: string;
  tone: SentimentRegimeTone;
  from: string;
  to: string;
  days: number;
}

export interface SentimentRegime {
  version: string;
  current: SentimentRegimeCurrent | null;
  segments: SentimentRegimeSegment[];
}

export interface SentimentMetricStats {
  sampleCount: number;
  min: number | null;
  max: number | null;
  mean: number | null;
  median: number | null;
  p10: number | null;
  p25: number | null;
  p75: number | null;
  p90: number | null;
  std: number | null;
}

export interface SentimentMetricBin {
  from: number;
  to: number;
  count: number;
}

export interface SentimentMetricStreak {
  side: "above" | "below" | null;
  days: number;
  median: number | null;
}

export interface SentimentMetricPeer {
  key: SentimentMetricKey;
  label: string;
  unit: SentimentMetricUnit;
  latestValue: number | null;
  latestDate: string | null;
  lowSample: boolean;
}

export interface SentimentMetricDetail {
  metric: SentimentMetricMeta;
  asOf: string | null;
  asOfQuality: AsOfQuality;
  window: SentimentTrendWindow;
  latest: SentimentMetricPoint | null;
  previous: SentimentMetricPoint | null;
  delta: number | null;
  percentileRank: number | null;
  stats: SentimentMetricStats | null;
  distribution: SentimentMetricBin[];
  streak: SentimentMetricStreak;
  series: SentimentMetricPoint[];
  peers: SentimentMetricPeer[];
  insufficientHistory: boolean;
  latestInterval: SentimentMetricInterval | null;
  sampleGate: SentimentMetricSampleGate | null;
  regime: SentimentRegime | null;
}

const mapMetricPoint = (p: BackendSentimentMetricPoint): SentimentMetricPoint => ({
  tradeDate: p.trade_date,
  value: p.value,
  n: p.n ?? null,
  lowSample: p.low_sample ?? false,
});

const mapRegime = (r: BackendSentimentRegime | null | undefined): SentimentRegime | null =>
  r
    ? {
        version: r.version,
        current: r.current
          ? {
              stage: r.current.stage,
              label: r.current.label,
              tone: r.current.tone,
              since: r.current.since,
              days: r.current.days,
              inputs: r.current.inputs.map((i) => ({
                key: i.key,
                label: i.label,
                value: i.value,
                percentile: i.percentile,
                unit: i.unit,
              })),
            }
          : null,
        segments: r.segments.map((s) => ({
          stage: s.stage,
          label: s.label,
          tone: s.tone,
          from: s.from,
          to: s.to,
          days: s.days,
        })),
      }
    : null;

const mapMetricStats = (s: BackendSentimentMetricStats | null): SentimentMetricStats | null =>
  s
    ? {
        sampleCount: s.sample_count,
        min: s.min,
        max: s.max,
        mean: s.mean,
        median: s.median,
        p10: s.p10,
        p25: s.p25,
        p75: s.p75,
        p90: s.p90,
        std: s.std,
      }
    : null;

/** `metric_key` 未知 → 后端 404；调用方不要拿"空图"兜底。 */
export function fetchSentimentMetric(
  metricKey: SentimentMetricKey,
  days = 250,
): Promise<SentimentMetricDetail> {
  return apiGet<BackendSentimentMetricDetail>(
    `/api/v1/market/sentiment/metrics/${metricKey}`,
    { days },
  ).then((b) => ({
    metric: {
      key: b.metric.key as SentimentMetricKey,
      label: b.metric.label,
      unit: b.metric.unit,
      group: b.metric.group,
      hotterWhen: b.metric.hotter_when,
      caliber: b.metric.caliber,
    },
    asOf: b.as_of,
    asOfQuality: b.as_of_quality ?? "partial",
    window: {
      days: b.window.days,
      from: b.window.from,
      to: b.window.to,
      samples: b.window.samples,
    },
    latest: b.latest ? mapMetricPoint(b.latest) : null,
    previous: b.previous ? mapMetricPoint(b.previous) : null,
    delta: b.delta,
    percentileRank: b.percentile_rank,
    stats: mapMetricStats(b.stats),
    distribution: b.distribution.map((bin) => ({ from: bin.from, to: bin.to, count: bin.count })),
    streak: { side: b.streak.side, days: b.streak.days, median: b.streak.median },
    series: b.series.map(mapMetricPoint),
    peers: b.peers.map((p) => ({
      key: p.key as SentimentMetricKey,
      label: p.label,
      unit: p.unit,
      latestValue: p.latest_value,
      latestDate: p.latest_date,
      lowSample: p.low_sample ?? false,
    })),
    insufficientHistory: b.insufficient_history,
    latestInterval: b.latest_interval
      ? {
          low: b.latest_interval.low,
          high: b.latest_interval.high,
          level: b.latest_interval.level,
          n: b.latest_interval.n,
          method: b.latest_interval.method,
        }
      : null,
    sampleGate: b.sample_gate
      ? {
          field: b.sample_gate.field,
          minN: b.sample_gate.min_n,
          excludedDays: b.sample_gate.excluded_days,
        }
      : null,
    regime: mapRegime(b.regime),
  }));
}

// ---------------------------------------------------------------------------
// 情绪脉搏（2026-10-19）：small multiples + 周期阶段 + 指数对比
//
// 一次读面拿全：9 个指标逐日序列、阶段区段、基准指数 5 日收益分位、背离段。
// 阶段/分位都由后端算（与 /sentiment/trends、/sentiment/metrics 同一口径）。
// ---------------------------------------------------------------------------

/** 允许的对比基准（后端白名单；本地只落了这两个指数）。 */
export const SENTIMENT_BENCHMARKS = [
  { code: "399006.SZ", label: "创业板指" },
  { code: "000001.SH", label: "上证指数" },
] as const;

export type SentimentBenchmarkCode = (typeof SENTIMENT_BENCHMARKS)[number]["code"];

interface BackendSentimentPulsePoint {
  trade_date: string;
  zt_count: number;
  dt_count: number;
  zb_count: number;
  broken_rate: number | null;
  yzt_avg_pct: number | null;
  yzt_avg_open_premium: number | null;
  yzt_n: number;
  promo_1to2: number | null;
  promo_1to2_n: number;
  promo_2to3: number | null;
  promo_2to3_n: number;
  max_streak: number;
}

interface BackendSentimentBenchmarkPoint {
  trade_date: string;
  close: number | null;
  pct_5d: number | null;
}

interface BackendSentimentComparisonPoint {
  trade_date: string;
  sentiment_pct: number | null;
  index_pct: number | null;
  index_pct_5d: number | null;
}

interface BackendSentimentDivergence {
  from: string;
  to: string;
  days: number;
  kind: "sentiment_stronger" | "index_stronger";
  label: string;
}

interface BackendSentimentPulse {
  as_of: string | null;
  as_of_quality?: AsOfQuality;
  window: BackendSentimentTrendWindow;
  series: BackendSentimentPulsePoint[];
  regime: BackendSentimentRegime;
  benchmark: {
    ts_code: string;
    name: string;
    points: BackendSentimentBenchmarkPoint[];
  };
  comparison: BackendSentimentComparisonPoint[];
  divergence: BackendSentimentDivergence[];
  current_divergence: BackendSentimentDivergence | null;
}

export interface SentimentPulsePoint {
  tradeDate: string;
  ztCount: number;
  dtCount: number;
  zbCount: number;
  brokenRate: number | null;
  yztAvgPct: number | null;
  yztAvgOpenPremium: number | null;
  yztN: number;
  promo1to2: number | null;
  promo1to2N: number;
  promo2to3: number | null;
  promo2to3N: number;
  maxStreak: number;
}

export interface SentimentBenchmarkPoint {
  tradeDate: string;
  close: number | null;
  pct5d: number | null;
}

export interface SentimentComparisonPoint {
  tradeDate: string;
  sentimentPct: number | null;
  indexPct: number | null;
  indexPct5d: number | null;
}

export interface SentimentDivergence {
  from: string;
  to: string;
  days: number;
  kind: "sentiment_stronger" | "index_stronger";
  label: string;
}

export interface SentimentPulse {
  asOf: string | null;
  asOfQuality: AsOfQuality;
  window: SentimentTrendWindow;
  series: SentimentPulsePoint[];
  regime: SentimentRegime;
  benchmark: {
    tsCode: string;
    name: string;
    points: SentimentBenchmarkPoint[];
  };
  comparison: SentimentComparisonPoint[];
  divergence: SentimentDivergence[];
  currentDivergence: SentimentDivergence | null;
}

/**
 * 情绪脉搏。`days` 只影响展示/对比窗口；阶段分位恒用 250 日 PIT 窗口（后端保证）。
 * `benchmark` 未知 → 404；调用方从 `SENTIMENT_BENCHMARKS` 取值，不手写字符串。
 */
export function fetchSentimentPulse(
  days = 60,
  benchmark: SentimentBenchmarkCode = "399006.SZ",
): Promise<SentimentPulse> {
  return apiGet<BackendSentimentPulse>("/api/v1/market/sentiment/pulse", {
    days,
    benchmark,
  }).then((b) => ({
    asOf: b.as_of,
    asOfQuality: b.as_of_quality ?? "partial",
    window: {
      days: b.window.days,
      from: b.window.from,
      to: b.window.to,
      samples: b.window.samples,
    },
    series: b.series.map((p) => ({
      tradeDate: p.trade_date,
      ztCount: p.zt_count,
      dtCount: p.dt_count,
      zbCount: p.zb_count,
      brokenRate: p.broken_rate,
      yztAvgPct: p.yzt_avg_pct,
      yztAvgOpenPremium: p.yzt_avg_open_premium,
      yztN: p.yzt_n,
      promo1to2: p.promo_1to2,
      promo1to2N: p.promo_1to2_n,
      promo2to3: p.promo_2to3,
      promo2to3N: p.promo_2to3_n,
      maxStreak: p.max_streak,
    })),
    regime: mapRegime(b.regime)!,
    benchmark: {
      tsCode: b.benchmark.ts_code,
      name: b.benchmark.name,
      points: b.benchmark.points.map((p) => ({
        tradeDate: p.trade_date,
        close: p.close,
        pct5d: p.pct_5d,
      })),
    },
    comparison: b.comparison.map((p) => ({
      tradeDate: p.trade_date,
      sentimentPct: p.sentiment_pct,
      indexPct: p.index_pct,
      indexPct5d: p.index_pct_5d,
    })),
    divergence: b.divergence.map((d) => ({
      from: d.from,
      to: d.to,
      days: d.days,
      kind: d.kind,
      label: d.label,
    })),
    currentDivergence: b.current_divergence
      ? {
          from: b.current_divergence.from,
          to: b.current_divergence.to,
          days: b.current_divergence.days,
          kind: b.current_divergence.kind,
          label: b.current_divergence.label,
        }
      : null,
  }));
}
