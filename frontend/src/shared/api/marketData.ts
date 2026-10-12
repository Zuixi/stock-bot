import { apiGet } from "./client";
import {
  mapNorthboundList,
  mapStaleList,
  type BackendNorthboundEnvelope,
  type BackendSnapshotEnvelope,
  type NorthboundEnvelope,
  type StaleEnvelope,
} from "./marketEnvelope";

// ---- 后端原始 payload（snake_case，勿直接外漏给 UI 层） ----

interface BackendGlobalIndexCard {
  ts_code: string;
  name: string;
  market: string;
  region: "asia" | "americas";
  price: number | null;
  change: number | null;
  pct_change: number | null;
  spark: number[];
  updated_at: string;
  source: "realtime" | "eod";
}

interface BackendSectorMoneyflowItem {
  board_code: string;
  board_name: string | null;
  pct_change: number | null;
  main_net_inflow: number | null;
  super_large_net: number | null;
  large_net: number | null;
  main_net_ratio: number | null;
  up_count: number | null;
  down_count: number | null;
  lead_stock_name: string | null;
  lead_stock_code: string | null;
  lead_stock_pct: number | null;
}

interface BackendMarketMoneyflow {
  today: {
    total: {
      amount: number | null;
      main_net: number | null;
      super_large_net: number | null;
      large_net: number | null;
      mid_net: number | null;
      small_net: number | null;
    } | null;
    markets: Array<{
      code: string | null;
      name: string | null;
      main_net: number | null;
      super_large_net: number | null;
      large_net: number | null;
      mid_net: number | null;
      small_net: number | null;
      main_ratio: number | null;
    }>;
  } | null;
  history: Array<{
    date: string;
    main_net: number | null;
    super_large_net: number | null;
    large_net: number | null;
    mid_net: number | null;
    small_net: number | null;
    main_ratio: number | null;
    close: number | null;
    pct_change: number | null;
    amount: number | null;
  }>;
  /** 历史表最近日 ISO；表空时为 null（后端 Task 5 起返回）。 */
  history_as_of?: string | null;
  /** 历史表陈旧度（自然日差，可能为负）；表空时为 null。 */
  history_stale_days?: number | null;
}

interface BackendNorthboundPoint {
  date: string;
  net_amount: number | null;
}

interface BackendDragonTigerItem {
  trade_date: string;
  ts_code: string;
  symbol: string;
  name: string | null;
  close: number | null;
  pct_change: number | null;
  turnover_rate: number | null;
  amount: number | null;
  l_buy: number | null;
  l_sell: number | null;
  l_amount: number | null;
  net_amount: number | null;
  reason: string;
}

interface BackendBlockTradeItem {
  trade_date: string;
  ts_code: string;
  symbol: string;
  name: string | null;
  price: number | null;
  volume: number | null;
  amount: number | null;
  buyer: string | null;
  seller: string | null;
}

interface BackendShareFloatItem {
  ann_date: string | null;
  float_date: string;
  ts_code: string;
  symbol: string;
  name: string | null;
  float_share: number | null;
  float_ratio: number | null;
  holder_name: string | null;
  share_type: string | null;
}

interface BackendRepurchaseItem {
  ann_date: string;
  ts_code: string;
  symbol: string;
  name: string | null;
  proc: string;
  end_date: string | null;
  exp_date: string | null;
  vol: number | null;
  amount: number | null;
}

interface BackendAnnouncementItem {
  announcement_id: string;
  sec_code: string;
  sec_name: string | null;
  title: string;
  announce_time: string;
  category: "report" | "event";
  pdf_url: string | null;
}

// ── Task 6b：板块趋势（GET /market/sector-trends，读时计算，不物化）──────────────

interface BackendSectorTrendPoint {
  trade_date: string;
  main_net_inflow: number | null;
  pct_change: number | null;
  up_count: number | null;
  down_count: number | null;
  /** 板块涨停家数：只有 concept 维度能算；`null` = 不适用/缺数据（**不是 0 家**）。 */
  lu_count: number | null;
}

interface BackendSectorTrendBoard {
  board_code: string;
  board_name: string | null;
  main_net_inflow: number | null;
  pct_change: number | null;
  up_count: number | null;
  down_count: number | null;
  streak_on_top_n: number;
  max_streak_on_top_n: number;
  streak_inflow: number;
  max_streak_inflow: number;
  streak_outflow: number;
  max_streak_outflow: number;
  series?: BackendSectorTrendPoint[];
}

interface BackendSectorTrendWindow {
  days: number;
  observed_days: number;
  from: string | null;
  to: string | null;
  samples: number;
}

interface BackendLeaderChange {
  trade_date: string;
  board_code: string;
  board_name: string | null;
}

interface BackendSectorTrends {
  dimension: string;
  as_of: string | null;
  available_from: string | null;
  attribution: "current_sw_snapshot" | "point_in_time";
  attribution_note: string;
  lu_count_source: "point_in_time_membership" | "unavailable";
  top_n: number;
  window: BackendSectorTrendWindow;
  boards?: BackendSectorTrendBoard[];
  missing_days?: string[];
  leader_change_count?: number;
  leader_changes?: BackendLeaderChange[];
}

// ---- 前端模型（camelCase） ----

export interface GlobalIndexCard {
  tsCode: string;
  name: string;
  market: string;
  region: "asia" | "americas";
  price: number | null;
  change: number | null;
  pctChange: number | null;
  spark: number[];
  updatedAt: string;
  source: "realtime" | "eod";
}

export interface SectorMoneyflowItem {
  boardCode: string;
  boardName: string | null;
  pctChange: number | null;
  mainNetInflow: number | null; // 元
  superLargeNet: number | null;
  largeNet: number | null;
  mainNetRatio: number | null;
  upCount: number | null;
  downCount: number | null;
  leadStockName: string | null; // 主力净流入最大股
  leadStockCode: string | null;
  leadStockPct: number | null; // %
}

export interface NorthboundPoint {
  date: string;
  netAmount: number | null; // 万元
}

export interface MarketMoneyflowDay {
  date: string;
  mainNet: number | null; // 元
  superLargeNet: number | null;
  largeNet: number | null;
  midNet: number | null;
  smallNet: number | null;
  mainRatio: number | null; // %
  close: number | null;
  pctChange: number | null;
  amount: number | null; // 元
}

export interface MarketMoneyflowMarket {
  code: string | null;
  name: string | null;
  mainNet: number | null;
  superLargeNet: number | null;
  largeNet: number | null;
  midNet: number | null;
  smallNet: number | null;
  mainRatio: number | null;
}

export interface MarketMoneyflow {
  /** 历史序列最近日（`history` 的 asOf 口径）；表空时为 null。Phase 1 徽标消费。 */
  historyAsOf: string | null;
  historyStaleDays: number | null;
  today: {
    total: {
      amount: number | null;
      mainNet: number | null;
      superLargeNet: number | null;
      largeNet: number | null;
      midNet: number | null;
      smallNet: number | null;
    } | null;
    markets: MarketMoneyflowMarket[];
  } | null;
  history: MarketMoneyflowDay[];
}

export function fetchMarketMoneyflow(): Promise<MarketMoneyflow> {
  return apiGet<BackendMarketMoneyflow>("/api/v1/market/market-moneyflow").then(mapMarketMoneyflow);
}

export interface DragonTigerItem {
  tradeDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  close: number | null;
  pctChange: number | null;
  turnoverRate: number | null;
  netAmount: number | null; // 元
  reason: string;
}

export interface BlockTradeItem {
  tradeDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  price: number | null; // 元
  volume: number | null; // 万股
  amount: number | null; // 万元
  buyer: string | null;
  seller: string | null;
}

export interface ShareFloatItem {
  annDate: string | null;
  floatDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  floatShare: number | null; // 万股
  floatRatio: number | null; // %
  holderName: string | null;
  shareType: string | null;
}

export interface RepurchaseItem {
  annDate: string;
  tsCode: string;
  symbol: string;
  name: string | null;
  proc: string;
  endDate: string | null;
  vol: number | null; // 股
  amount: number | null; // 元
}

export interface AnnouncementItem {
  announcementId: string;
  secCode: string;
  secName: string | null;
  title: string;
  announceTime: string;
  category: "report" | "event";
  pdfUrl: string | null;
}

/** 板块维度（与后端 `Literal["industry","concept","region"]` 对齐）。 */
export type SectorDimension = "industry" | "concept" | "region";

/**
 * 归属口径（Ruling 22）只以 `SectorTrends.attributionNote` 一个真源上屏（Task 8）：
 * 后端另有枚举 `attribution`（`current_sw_snapshot` | `point_in_time`），前端不消费，
 * 故不映射、也不在本层另立一个可能与文案不一致的类型。
 */

/** 板块涨停家数的归属来源；`unavailable` 时前端**不得**渲染家数（缺失 ≠ 0 家）。 */
export type SectorLuCountSource = "point_in_time_membership" | "unavailable";

/** 板块在某个**完整日**的观测（缺完整日不出现在序列里）。 */
export interface SectorTrendPoint {
  tradeDate: string;
  mainNetInflow: number | null; // 元
  pctChange: number | null; // %
  upCount: number | null;
  downCount: number | null;
  luCount: number | null;
}

/** 单板块在窗口内的连续性统计（只在完整日上计算，缺日既不续接也不打断）。 */
export interface SectorTrendBoard {
  boardCode: string;
  boardName: string | null;
  /** **最后完整日**的值；`null` = 该行上游缺值（**不是 0 流入**）→ 前端 `--`。 */
  mainNetInflow: number | null; // 元
  pctChange: number | null;
  upCount: number | null;
  downCount: number | null;
  /** 上榜口径：当日 `mainNetInflow` 降序前 `topN`。 */
  streakOnTopN: number;
  maxStreakOnTopN: number;
  streakInflow: number;
  maxStreakInflow: number;
  streakOutflow: number;
  maxStreakOutflow: number;
  series: SectorTrendPoint[];
}

export interface SectorTrendWindow {
  days: number;
  /** 实际观察到的交易日跨度（可能 < `days`：数据起点附近/该维度行集更短）。 */
  observedDays: number;
  from: string | null;
  to: string | null;
  samples: number;
}

/** 龙头更替事件（`boardCode`/`boardName` 是**当日新龙头**）。 */
export interface LeaderChange {
  tradeDate: string;
  boardCode: string;
  boardName: string | null;
}

/**
 * 板块趋势（`GET /market/sector-trends`）。
 *
 * 三条硬约束见后端 schema：`attributionNote`（归属口径文案，**唯一**的归属真源）必填
 * 且如实；`availableFrom`（可为 `null` = 该维度还没有任何完整日）；`missingDays` 显式列
 * 缺日，**缺日不得当成 0 流入**。
 *
 * 为什么**不**把后端 `attribution`（枚举）也映射上来（Task 8）：它与 `attributionNote`
 * 是同一件事的两个来源，前端只用后者渲染 ⇒ 映射上来只会成为无人消费的第二真源，
 * 并让"标注随维度切换联动"这件事有两个可能不一致的入口。归属口径只保留 note + 维度。
 */
export interface SectorTrends {
  dimension: string;
  asOf: string | null;
  availableFrom: string | null;
  attributionNote: string;
  luCountSource: SectorLuCountSource;
  topN: number;
  window: SectorTrendWindow;
  boards: SectorTrendBoard[];
  missingDays: string[];
  leaderChangeCount: number;
  leaderChanges: LeaderChange[];
}

// ---- snake → camel 映射 ----

const mapCard = (b: BackendGlobalIndexCard): GlobalIndexCard => ({
  tsCode: b.ts_code,
  name: b.name,
  market: b.market,
  region: b.region,
  price: b.price,
  change: b.change,
  pctChange: b.pct_change,
  spark: b.spark,
  updatedAt: b.updated_at,
  source: b.source,
});

const mapMarketMoneyflow = (b: BackendMarketMoneyflow): MarketMoneyflow => ({
  historyAsOf: b.history_as_of ?? null,
  historyStaleDays: b.history_stale_days ?? null,
  today: b.today
    ? {
        total: b.today.total
          ? {
              amount: b.today.total.amount,
              mainNet: b.today.total.main_net,
              superLargeNet: b.today.total.super_large_net,
              largeNet: b.today.total.large_net,
              midNet: b.today.total.mid_net,
              smallNet: b.today.total.small_net,
            }
          : null,
        markets: (b.today.markets ?? []).map((m) => ({
          code: m.code,
          name: m.name,
          mainNet: m.main_net,
          superLargeNet: m.super_large_net,
          largeNet: m.large_net,
          midNet: m.mid_net,
          smallNet: m.small_net,
          mainRatio: m.main_ratio,
        })),
      }
    : null,
  history: (b.history ?? []).map((h) => ({
    date: h.date,
    mainNet: h.main_net,
    superLargeNet: h.super_large_net,
    largeNet: h.large_net,
    midNet: h.mid_net,
    smallNet: h.small_net,
    mainRatio: h.main_ratio,
    close: h.close,
    pctChange: h.pct_change,
    amount: h.amount,
  })),
});

const mapSectorMoneyflow = (b: BackendSectorMoneyflowItem): SectorMoneyflowItem => ({
  boardCode: b.board_code,
  boardName: b.board_name,
  pctChange: b.pct_change,
  mainNetInflow: b.main_net_inflow,
  superLargeNet: b.super_large_net,
  largeNet: b.large_net,
  mainNetRatio: b.main_net_ratio,
  upCount: b.up_count,
  downCount: b.down_count,
  leadStockName: b.lead_stock_name,
  leadStockCode: b.lead_stock_code,
  leadStockPct: b.lead_stock_pct,
});

const mapNorthbound = (b: BackendNorthboundPoint): NorthboundPoint => ({
  date: b.date,
  netAmount: b.net_amount,
});

const mapDragonTiger = (b: BackendDragonTigerItem): DragonTigerItem => ({
  tradeDate: b.trade_date,
  tsCode: b.ts_code,
  symbol: b.symbol,
  name: b.name,
  close: b.close,
  pctChange: b.pct_change,
  turnoverRate: b.turnover_rate,
  netAmount: b.net_amount,
  reason: b.reason,
});

const mapBlockTrade = (b: BackendBlockTradeItem): BlockTradeItem => ({
  tradeDate: b.trade_date,
  tsCode: b.ts_code,
  symbol: b.symbol,
  name: b.name,
  price: b.price,
  volume: b.volume,
  amount: b.amount,
  buyer: b.buyer,
  seller: b.seller,
});

const mapShareFloat = (b: BackendShareFloatItem): ShareFloatItem => ({
  annDate: b.ann_date,
  floatDate: b.float_date,
  tsCode: b.ts_code,
  symbol: b.symbol,
  name: b.name,
  floatShare: b.float_share,
  floatRatio: b.float_ratio,
  holderName: b.holder_name,
  shareType: b.share_type,
});

const mapRepurchase = (b: BackendRepurchaseItem): RepurchaseItem => ({
  annDate: b.ann_date,
  tsCode: b.ts_code,
  symbol: b.symbol,
  name: b.name,
  proc: b.proc,
  endDate: b.end_date,
  vol: b.vol,
  amount: b.amount,
});

const mapAnnouncement = (b: BackendAnnouncementItem): AnnouncementItem => ({
  announcementId: b.announcement_id,
  secCode: b.sec_code,
  secName: b.sec_name,
  title: b.title,
  announceTime: b.announce_time,
  category: b.category,
  pdfUrl: b.pdf_url,
});

// ---- 板块趋势（Task 6b）映射 ----

const mapSectorTrendPoint = (p: BackendSectorTrendPoint): SectorTrendPoint => ({
  tradeDate: p.trade_date,
  mainNetInflow: p.main_net_inflow,
  pctChange: p.pct_change,
  upCount: p.up_count,
  downCount: p.down_count,
  luCount: p.lu_count,
});

const mapSectorTrendBoard = (b: BackendSectorTrendBoard): SectorTrendBoard => ({
  boardCode: b.board_code,
  boardName: b.board_name,
  mainNetInflow: b.main_net_inflow,
  pctChange: b.pct_change,
  upCount: b.up_count,
  downCount: b.down_count,
  streakOnTopN: b.streak_on_top_n,
  maxStreakOnTopN: b.max_streak_on_top_n,
  streakInflow: b.streak_inflow,
  maxStreakInflow: b.max_streak_inflow,
  streakOutflow: b.streak_outflow,
  maxStreakOutflow: b.max_streak_outflow,
  series: (b.series ?? []).map(mapSectorTrendPoint),
});

const mapSectorTrends = (b: BackendSectorTrends): SectorTrends => ({
  dimension: b.dimension,
  asOf: b.as_of,
  availableFrom: b.available_from,
  attributionNote: b.attribution_note,
  luCountSource: b.lu_count_source,
  topN: b.top_n,
  window: {
    days: b.window.days,
    // 缺省回落到"请求值"会让短观测跨度看起来像短请求 —— 拿不到就按 samples 自陈
    observedDays: b.window.observed_days ?? b.window.samples,
    from: b.window.from,
    to: b.window.to,
    samples: b.window.samples,
  },
  boards: (b.boards ?? []).map(mapSectorTrendBoard),
  // 缺日原样透传：`missingDays` 是「这天没有完整快照」的唯一信号，前端不得补 0
  missingDays: b.missing_days ?? [],
  leaderChangeCount: b.leader_change_count ?? 0,
  leaderChanges: (b.leader_changes ?? []).map((c) => ({
    tradeDate: c.trade_date,
    boardCode: c.board_code,
    boardName: c.board_name,
  })),
});

// ---- 请求函数 ----

export function fetchGlobalIndices(): Promise<GlobalIndexCard[]> {
  return apiGet<BackendGlobalIndexCard[]>("/api/v1/market/global-indices").then((rows) => rows.map(mapCard));
}

/**
 * 板块主力资金流。后端返回快照 envelope，mapper 解包后返回 `{items, asOf, staleDays}`，
 * 消费端读 `.items`，`asOf`（该快照实际最近日）与 `staleDays` 见 `marketEnvelope.ts`。
 */
export function fetchSectorMoneyflow(
  dimension: "industry" | "concept" | "region",
  limit = 15,
): Promise<StaleEnvelope<SectorMoneyflowItem>> {
  return apiGet<BackendSnapshotEnvelope<BackendSectorMoneyflowItem>>("/api/v1/market/sector-moneyflow", {
    dimension,
    limit,
  }).then((b) => mapStaleList(b, mapSectorMoneyflow));
}

/**
 * 北向净流入序列。同 {@link fetchSectorMoneyflow}：返回 `{items, asOf, staleDays, sourceStatus}`
 * 信封（上游停更时 `sourceStatus` 为 `discontinued`）。
 */
export function fetchNorthbound(days = 30): Promise<NorthboundEnvelope<NorthboundPoint>> {
  return apiGet<BackendNorthboundEnvelope<BackendNorthboundPoint>>("/api/v1/market/northbound", { days }).then((b) =>
    mapNorthboundList(b, mapNorthbound),
  );
}

export function fetchDragonTiger(limit = 15): Promise<DragonTigerItem[]> {
  return apiGet<BackendDragonTigerItem[]>("/api/v1/market/dragon-tiger", { limit }).then((rows) =>
    rows.map(mapDragonTiger),
  );
}

export function fetchBlockTrades(symbol?: string, limit = 15): Promise<BlockTradeItem[]> {
  return apiGet<BackendBlockTradeItem[]>("/api/v1/market/block-trades", { symbol, limit }).then((rows) =>
    rows.map(mapBlockTrade),
  );
}

export function fetchShareFloats(symbol?: string, limit = 30): Promise<ShareFloatItem[]> {
  return apiGet<BackendShareFloatItem[]>("/api/v1/market/share-floats", { symbol, limit }).then((rows) =>
    rows.map(mapShareFloat),
  );
}

export function fetchRepurchases(symbol?: string, limit = 30): Promise<RepurchaseItem[]> {
  return apiGet<BackendRepurchaseItem[]>("/api/v1/market/repurchases", { symbol, limit }).then((rows) =>
    rows.map(mapRepurchase),
  );
}

export function fetchAnnouncements(symbol?: string, limit = 30): Promise<AnnouncementItem[]> {
  return apiGet<BackendAnnouncementItem[]>("/api/v1/market/announcements", { symbol, limit }).then((rows) =>
    rows.map(mapAnnouncement),
  );
}

/**
 * 板块趋势（连续上榜 / 连续净流入-流出 / 龙头更替 / 板块涨停家数）。
 *
 * `days` 只统计**完整日**（`eod_full`）；窗口内缺完整日的交易日进 `missingDays`，
 * **绝不**当成 0 流入。`topN` 是「上榜」口径（当日主力净流入前 N），显式写进响应。
 */
export function fetchSectorTrends(
  dimension: SectorDimension = "industry",
  days = 60,
  topN = 20,
): Promise<SectorTrends> {
  return apiGet<BackendSectorTrends>("/api/v1/market/sector-trends", {
    dimension,
    days,
    top_n: topN,
  }).then(mapSectorTrends);
}
