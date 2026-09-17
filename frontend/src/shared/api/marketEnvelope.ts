/**
 * 市场列表端点的统一信封（后端 `MarketListOut` / 快照 envelope）+ 口径元数据约定。
 *
 * 后端 Task 2/5 把原本返回裸数组的 6 个端点包成 `{as_of..., items}`。前端在这里做两件事：
 *
 * 1. `map*List` 解包 `.items`，导出函数仍返回**数组** —— 组件零改动，渲染与空态语义不变；
 * 2. 口径元数据（camelCase）以自有属性挂在返回的数组上，Phase 1 的徽标 / query key 直接消费。
 *
 * TS 里「数组形状不变 + 追加元数据」只能这么做（数组即对象）。两个副作用 Phase 1 需知：
 * - `data = []` 兜底默认值、`[...list]` / `list.slice()` 副本都拿不到元数据 —— 读元数据一律走
 *   {@link listMeta} / {@link staleListMeta} / {@link northboundListMeta}（对裸数组安全回落）；
 * - 带自有属性的数组不再是「纯数组」，React Query 的 structural sharing 会跳过它
 *   （`isPlainArray` 为 false）→ 每次 refetch 都是新引用。渲染输出不变，仅多一次 diff。
 */

/** 日级数据的完整性口径，与后端 `app/schemas/market.py::AsOfQuality` 对齐。 */
export type AsOfQuality = "complete" | "partial" | "fallback";

/** 上游数据源状态；`discontinued` = 已停更（如北向 `northbound_daily` 断流）。 */
export type SourceStatus = "live" | "discontinued";

/** `MarketListOut` 信封的元数据：判据日 + 口径。 */
export interface AsOfMeta {
  /** 判据日（ISO `YYYY-MM-DD`）；库内无任何行情时为 null。 */
  asOf: string | null;
  asOfQuality: AsOfQuality;
  /** `fallback` / `partial` 的原因码，如 `latest_day_incomplete`。 */
  asOfReason: string | null;
}

/** 快照类信封的元数据：最近快照日 + 陈旧度（自然日差，可能为负 = 表内有未来日）。 */
export interface StaleMeta {
  asOf: string | null;
  staleDays: number | null;
}

/** 北向信封：陈旧度之外再带停更标注。 */
export interface NorthboundMeta extends StaleMeta {
  sourceStatus: SourceStatus;
}

/**
 * 数组 + 挂在数组上的元数据。仍是数组：`.map` / `.length` / `[...list]` 与作为 `T[]` 形参
 * 全部照旧，故本任务无需改动任何组件。
 */
export type AnnotatedList<T, Meta> = T[] & Meta;

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

// ---- 解包（后端信封 → 数组 + 元数据） ----

const AS_OF_FALLBACK: AsOfMeta = { asOf: null, asOfQuality: "partial", asOfReason: null };
const STALE_FALLBACK: StaleMeta = { asOf: null, staleDays: null };
const NORTHBOUND_FALLBACK: NorthboundMeta = { ...STALE_FALLBACK, sourceStatus: "discontinued" };

/** 解包 `MarketListOut`：`.items` 逐项映射后附上口径元数据。 */
export function mapMarketList<Raw, T>(
  b: BackendMarketListOut<Raw>,
  map: (raw: Raw) => T,
): AnnotatedList<T, AsOfMeta> {
  return Object.assign((b.items ?? []).map(map), {
    asOf: b.as_of ?? null,
    asOfQuality: b.as_of_quality ?? AS_OF_FALLBACK.asOfQuality,
    asOfReason: b.as_of_reason ?? null,
  });
}

/** 解包快照 envelope：`.items` 逐项映射后附上 `asOf` / `staleDays`。 */
export function mapStaleList<Raw, T>(
  b: BackendSnapshotEnvelope<Raw>,
  map: (raw: Raw) => T,
): AnnotatedList<T, StaleMeta> {
  return Object.assign((b.items ?? []).map(map), {
    asOf: b.as_of ?? null,
    staleDays: b.stale_days ?? null,
  });
}

/** 解包北向 envelope：`.items` 逐项映射后附上 `asOf` / `staleDays` / `sourceStatus`。 */
export function mapNorthboundList<Raw, T>(
  b: BackendNorthboundEnvelope<Raw>,
  map: (raw: Raw) => T,
): AnnotatedList<T, NorthboundMeta> {
  return Object.assign((b.items ?? []).map(map), {
    asOf: b.as_of ?? null,
    staleDays: b.stale_days ?? null,
    // 后端缺省即 `discontinued`：标注未知宁可从「停更」一侧保守兜底
    sourceStatus: b.source_status ?? NORTHBOUND_FALLBACK.sourceStatus,
  });
}

// ---- 元数据读取（对 `data = []` 兜底值 / 展开副本等裸数组安全） ----

/** 读列表口径元数据；裸数组回落 `null` / `partial`（与后端缺省口径一致）。 */
export function listMeta<T>(list: readonly T[] | undefined): AsOfMeta {
  const meta = list as Partial<AsOfMeta> | undefined;
  return {
    asOf: meta?.asOf ?? AS_OF_FALLBACK.asOf,
    asOfQuality: meta?.asOfQuality ?? AS_OF_FALLBACK.asOfQuality,
    asOfReason: meta?.asOfReason ?? AS_OF_FALLBACK.asOfReason,
  };
}

/** 读快照类列表的 `asOf` / `staleDays`；裸数组回落 null。 */
export function staleListMeta<T>(list: readonly T[] | undefined): StaleMeta {
  const meta = list as Partial<StaleMeta> | undefined;
  return {
    asOf: meta?.asOf ?? STALE_FALLBACK.asOf,
    staleDays: meta?.staleDays ?? STALE_FALLBACK.staleDays,
  };
}

/** 读北向列表的 `asOf` / `staleDays` / `sourceStatus`；裸数组回落 `discontinued`。 */
export function northboundListMeta<T>(list: readonly T[] | undefined): NorthboundMeta {
  const meta = list as Partial<NorthboundMeta> | undefined;
  return {
    asOf: meta?.asOf ?? NORTHBOUND_FALLBACK.asOf,
    staleDays: meta?.staleDays ?? NORTHBOUND_FALLBACK.staleDays,
    sourceStatus: meta?.sourceStatus ?? NORTHBOUND_FALLBACK.sourceStatus,
  };
}
