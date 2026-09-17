/**
 * 市场列表端点的统一信封（后端 `MarketListOut` / 快照 envelope）+ 口径元数据约定。
 *
 * 后端 Task 2/5 把原本返回裸数组的 6 个端点包成 `{as_of..., items}`。前端在 `map*List` 里
 * 解包 `.items` 并**返回常规信封对象** `{items, ...元数据}`（camelCase）：组件读 `.items`，
 * Phase 1 的徽标 / query key 直接读 `asOf` / `asOfQuality` / `staleDays` / `sourceStatus`。
 *
 * 为什么不是「数组 + 挂在数组上的元数据属性」：数组派生操作（展开 / `filter` / `slice` /
 * `structuredClone`）会静默丢属性，兜底读取器会把「丢了元数据」变成静默错标（谎报 partial /
 * discontinued）；带自有属性的数组还会让 React Query 跳过 structural sharing。信封对象没有
 * 这些坑，`items` 是纯数组，元数据只能显式读取。
 */

/** 日级数据的完整性口径，与后端 `app/schemas/market.py::AsOfQuality` 对齐。 */
export type AsOfQuality = "complete" | "partial" | "fallback";

/** 上游数据源状态；`discontinued` = 已停更（如北向 `northbound_daily` 断流）。 */
export type SourceStatus = "live" | "discontinued";

// ---- 后端原始 payload（snake_case，只在本文件的 mapper 里解包） ----

/**
 * `app/schemas/market.py::MarketListOut`（distribution / sectors / capital-flow / hot-boards）。
 *
 * 字段可缺省：`items` 由后端 `default_factory=list`，这里保留旧 mapper 的 `?? []` 空值语义；
 * 元数据缺省时回落后端默认值（`as_of_quality="partial"`），避免把未知口径谎报成 `complete`。
 */
export interface BackendMarketListOut<Raw> {
  as_of?: string | null;
  as_of_quality?: AsOfQuality;
  as_of_reason?: string | null;
  items?: Raw[];
}

/**
 * 快照 envelope（`SectorMoneyflowListOut`）：表空时 `as_of` / `stale_days` 均为 null。
 */
export interface BackendSnapshotEnvelope<Raw> {
  as_of?: string | null;
  stale_days?: number | null;
  items?: Raw[];
}

/** 热门板块 items 的产地，与后端 `app/schemas/market.py::HotBoardSource` 对齐。 */
export type HotBoardSource = "eastmoney_boards" | "local_grouping";

/**
 * `app/schemas/market.py::HotBoardsOut` = `MarketListOut` + 产地判别。
 *
 * 后端换源（East Money 板块体系）后回落本地分组时会置 `source="local_grouping"` +
 * `degraded_reason`，否则 `code=""`/`leaders=[]` 会被误读成"东财板块没有成分股"。
 */
export interface BackendHotBoardsOut<Raw> extends BackendMarketListOut<Raw> {
  source?: HotBoardSource | null;
  degraded_reason?: string | null;
}

/**
 * `app/schemas/market.py::BoardStockOut`（东财板块成分股，snake_case 裸数组）。
 *
 * 该端点**不套信封**（`response_model=list[BoardStockOut]`）：上游失败时后端直接 502，
 * 不会返回空数组——空数组只代表「这个板块真的没有成分股」。
 */
export interface BackendBoardStockOut {
  symbol: string;
  name?: string | null;
  pct_change?: number | null;
  /** 主力净流入（元）。 */
  main_net_inflow?: number | null;
}

/** `app/schemas/market_data.py::NorthboundSeriesOut`。 */
export interface BackendNorthboundEnvelope<Raw> extends BackendSnapshotEnvelope<Raw> {
  source_status?: SourceStatus;
}

// ---- 前端信封（camelCase；组件读 `.items`，Phase 1 读其余字段） ----

/** `MarketListOut` 的前端形状：判据日 + 完整性口径。 */
export interface MarketListEnvelope<T> {
  items: T[];
  /** 判据日（ISO `YYYY-MM-DD`）；库内无任何行情时为 null。 */
  asOf: string | null;
  asOfQuality: AsOfQuality;
  /** `fallback` / `partial` 的原因码，如 `latest_day_incomplete`。 */
  asOfReason: string | null;
}

/** 快照类信封的前端形状：最近快照日 + 陈旧度（自然日差，可能为负 = 表内有未来日）。 */
export interface StaleEnvelope<T> {
  items: T[];
  asOf: string | null;
  staleDays: number | null;
}

/** 北向信封：陈旧度之外再带停更标注。 */
export interface NorthboundEnvelope<T> extends StaleEnvelope<T> {
  sourceStatus: SourceStatus;
}

/** 热门板块信封：`MarketListOut` 元数据 + 产地（东财板块 / 本地分组回落）。 */
export interface HotBoardsEnvelope<T> extends MarketListEnvelope<T> {
  source: HotBoardSource;
  /** 回落原因码（东财不可用时 `eastmoney_unavailable`）；正常为 null。 */
  degradedReason: string | null;
}

/** 成分股行前端形状（camelCase）。 */
export interface BoardStockRow {
  symbol: string;
  name: string | null;
  /** 涨跌幅（%）；缺报价时为 null（渲染 `--`，不回落到 0）。 */
  pctChange: number | null;
  /** 主力净流入（元）；缺值时为 null。 */
  mainNetInflow: number | null;
}

/**
 * 产地降级文案：东财来源（正常）返回 `null`，调用方据此决定是否上屏标注。
 *
 * 回落时 `code=""` / `leaders=[]`，若不上屏标注，读者会把「本地分组」误读成
 * 「东财板块没有成分股」——这正是 Task 14 review 指出的悬空字段。
 */
export function hotBoardDegradedText(
  source: HotBoardSource,
  degradedReason: string | null,
): string | null {
  if (source !== "local_grouping") return null;
  return degradedReason === "eastmoney_unavailable" ? "本地分组（东财板块不可用）" : "本地分组（降级）";
}

// ---- 解包（后端信封 → 前端信封） ----

/**
 * 解包 `MarketListOut`。默认 `T = Raw`：行内字段无需映射时直接 `mapMarketList(b)`，不必传恒等 lambda。
 */
export function mapMarketList<Raw, T = Raw>(
  b: BackendMarketListOut<Raw>,
  map?: (raw: Raw) => T,
): MarketListEnvelope<T> {
  return {
    items: (b.items ?? []).map((raw) => (map ? map(raw) : (raw as unknown as T))),
    asOf: b.as_of ?? null,
    asOfQuality: b.as_of_quality ?? "partial",
    asOfReason: b.as_of_reason ?? null,
  };
}

/**
 * 解包热门板块信封：`MarketListOut` 元数据之外再带 `source` / `degradedReason`。
 *
 * `source` 只认 `"eastmoney_boards"`，其余（缺字段、未知取值）一律按 `"local_grouping"`
 * 保守兜底——宁可把东财数据说成降级，也不能把降级数据冒充成东财板块。
 */
export function mapHotBoards<Raw, T = Raw>(
  b: BackendHotBoardsOut<Raw>,
  map?: (raw: Raw) => T,
): HotBoardsEnvelope<T> {
  return {
    ...mapMarketList(b, map),
    source: b.source === "eastmoney_boards" ? "eastmoney_boards" : "local_grouping",
    degradedReason: b.degraded_reason ?? null,
  };
}

/**
 * 解包 `GET /market/boards/{code}/stocks` 的裸数组：统一 snake→camel，缺值为 `null`。
 *
 * 不做「空数组 → 错误」的转换：空数组是合法语义（板块真的没有成分股），
 * 上游失败由 HTTP 502 表达，绝不能在这里把两种情况合并。
 */
export function mapBoardStocks(rows: BackendBoardStockOut[] | null | undefined): BoardStockRow[] {
  return (rows ?? []).map((raw) => ({
    symbol: raw.symbol,
    name: raw.name ?? null,
    pctChange: raw.pct_change ?? null,
    mainNetInflow: raw.main_net_inflow ?? null,
  }));
}

/** 解包快照 envelope：`.items` 逐项映射后附上 `asOf` / `staleDays`。 */
export function mapStaleList<Raw, T = Raw>(
  b: BackendSnapshotEnvelope<Raw>,
  map?: (raw: Raw) => T,
): StaleEnvelope<T> {
  return {
    items: (b.items ?? []).map((raw) => (map ? map(raw) : (raw as unknown as T))),
    asOf: b.as_of ?? null,
    staleDays: b.stale_days ?? null,
  };
}

/** 解包北向 envelope：`.items` 逐项映射后附上 `asOf` / `staleDays` / `sourceStatus`。 */
export function mapNorthboundList<Raw, T = Raw>(
  b: BackendNorthboundEnvelope<Raw>,
  map?: (raw: Raw) => T,
): NorthboundEnvelope<T> {
  return {
    items: (b.items ?? []).map((raw) => (map ? map(raw) : (raw as unknown as T))),
    asOf: b.as_of ?? null,
    staleDays: b.stale_days ?? null,
    // 后端缺省即 `discontinued`：标注未知宁可从「停更」一侧保守兜底
    sourceStatus: b.source_status ?? "discontinued",
  };
}
