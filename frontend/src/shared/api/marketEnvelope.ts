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
