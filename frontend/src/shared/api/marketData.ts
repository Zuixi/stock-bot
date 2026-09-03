import { apiGet } from "./client";

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
}

export interface NorthboundPoint {
  date: string;
  netAmount: number | null; // 万元
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

// ---- 请求函数 ----

export function fetchGlobalIndices(): Promise<GlobalIndexCard[]> {
  return apiGet<BackendGlobalIndexCard[]>("/api/v1/market/global-indices").then((rows) => rows.map(mapCard));
}

export function fetchSectorMoneyflow(dimension: "industry" | "concept", limit = 15): Promise<SectorMoneyflowItem[]> {
  return apiGet<BackendSectorMoneyflowItem[]>("/api/v1/market/sector-moneyflow", { dimension, limit }).then((rows) =>
    rows.map(mapSectorMoneyflow),
  );
}

export function fetchNorthbound(days = 30): Promise<NorthboundPoint[]> {
  return apiGet<BackendNorthboundPoint[]>("/api/v1/market/northbound", { days }).then((rows) => rows.map(mapNorthbound));
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
