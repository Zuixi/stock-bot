import { apiGet } from "./client";

/**
 * S1.4 · 申万行业三级下钻（SCD2 分类归属 + 7 项行情字段）。
 *
 * 口径纪律（与 `backend/app/schemas/sw_drilldown.py` 逐条对应，改动必须两边同改）：
 * - `asOf`   = **行情口径日**（后端 `market_day_service` 判据，T+1）；
 * - `scd2AsOf` = **分类归属口径日** = 最近一次成功 ingest 的日期 = 可还原能力的**上界**。
 *   两者不是同一天是常态：UI 必须分开展示，否则会把"我们还没采到那一天"误读成"那天没有这只股票"。
 * - 所有数值字段都可为 `null`（后端不填 0）：渲染 `--`，**不得** `?? 0`。
 * - `amount` 单位 = **元**（后端已把 TuShare 原生千元 ×1000）。
 */

export interface KlineMedian {
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  /** 参与中位数的家数 = 当日真有行情的家数（不是成分总数，那在 IndustryQuote.member_count） */
  quoted_count: number;
  up_count: number;
  down_count: number;
}

/** 最近一次 SCD2 成分 ingest 的覆盖摘要（缺数对账的读面入口，Fix round 1 · 评审 I5） */
export interface Scd2Coverage {
  last_run_as_of: string | null;
  last_run_status: string | null;
  fully_ingested: boolean;
  index_codes: number;
  rows_inserted: number;
  rows_updated: number;
  zero_row_indices: number;
  detail: string | null;
}

/** 一个行业节点在某交易日的 7 项行情字段聚合 */
export interface IndustryQuote {
  code: string;
  name: string;
  level: number;
  parent_code: string | null;
  member_count: number;
  quoted_count: number;
  up_count: number;
  down_count: number;
  /**
   * 成员来源：`level` = 节点**自身层级**的 SCD2 成员（正确）；
   * `rollup` = 该层级没有记录，退回"其下 L3 的并集"**估算** —— 与上游**不等价**
   * （实测 24/31 个一级行业偏少），聚合出的成交额等也会偏少。
   */
  member_source: "level" | "rollup";
  member_source_note: string | null;
  /** ① K 线（成分股 OHLC 中位数，不是指数点位） */
  kline_median: KlineMedian;
  /** ② 涨跌幅（成交额加权，%） */
  pct_chg_weighted: number | null;
  pct_chg_covered: number;
  /** ③ 换手率（成交额加权，%） */
  turnover_rate_weighted: number | null;
  turnover_rate_covered: number;
  /** ④ 成交量（股） */
  volume: number | null;
  volume_covered: number;
  /** ⑤ 量比（成交量加权） */
  volume_ratio_weighted: number | null;
  volume_ratio_covered: number;
  /** ⑥ 振幅（%，成分股振幅均值） */
  amplitude: number | null;
  amplitude_covered: number;
  /** ⑦ 成交额（元） */
  amount: number | null;
  amount_covered: number;
}

export interface IndustryLevelItem {
  code: string;
  name: string;
  level: number;
  stock_count: number;
  metrics: IndustryQuote | null;
}

export interface IndustryLevelResponse {
  as_of: string | null;
  as_of_quality: "complete" | "partial" | "fallback";
  as_of_reason: string | null;
  /** 分类归属口径日（最近一次成功 ingest）；null = 尚无 SCD2 数据 */
  scd2_as_of: string | null;
  scd2_available: boolean;
  scd2_note: string | null;
  scd2_coverage: Scd2Coverage;
  level: number;
  parent: IndustryLevelItem | null;
  items: IndustryLevelItem[];
}

export interface IndustryMember {
  symbol: string;
  stock_code: string;
  name: string | null;
  /** SCD2 区间（本行生效起） */
  effective_from: string;
  effective_to: string | null;
  quoted: boolean;
  // ── 7 项字段（个股层）──────────────────────────────────────────────
  open: number | null;
  high: number | null;
  low: number | null;
  close: number | null;
  pct_chg: number | null;
  turnover_rate: number | null;
  volume: number | null;
  volume_ratio: number | null;
  amplitude: number | null;
  /** 元 */
  amount: number | null;
}

export interface IndustryMembersResponse {
  as_of: string | null;
  as_of_quality: "complete" | "partial" | "fallback";
  as_of_reason: string | null;
  scd2_as_of: string | null;
  scd2_available: boolean;
  scd2_note: string | null;
  scd2_coverage: Scd2Coverage;
  chain: IndustryLevelItem[];
  metrics: IndustryQuote | null;
  items: IndustryMember[];
}

function drillParams(asOf?: string) {
  return asOf ? { as_of: asOf } : undefined;
}

/** 某一层的下钻列表：level=1 → 31 个一级行业；level=2/3 需给上层代码。 */
export function fetchIndustryLevel(
  level: 1 | 2 | 3,
  opts: { level1Code?: string; level2Code?: string; asOf?: string } = {}
): Promise<IndustryLevelResponse> {
  return apiGet<IndustryLevelResponse>(`/api/v1/market/sw-industry/drill/levels/${level}`, {
    level1_code: opts.level1Code,
    level2_code: opts.level2Code,
    ...drillParams(opts.asOf),
  });
}

/** 单个行业节点的 7 项聚合。 */
export function fetchIndustryMetrics(opts: {
  level1Code: string;
  level2Code?: string;
  level3Code?: string;
  asOf?: string;
}): Promise<IndustryQuote> {
  return apiGet<IndustryQuote>("/api/v1/market/sw-industry/drill/metrics", {
    level1_code: opts.level1Code,
    level2_code: opts.level2Code,
    level3_code: opts.level3Code,
    ...drillParams(opts.asOf),
  });
}

/** 末级（L3）成分股列表：逐只 7 项字段。 */
export function fetchIndustryMembers(opts: {
  level1Code: string;
  level2Code: string;
  level3Code: string;
  asOf?: string;
}): Promise<IndustryMembersResponse> {
  return apiGet<IndustryMembersResponse>("/api/v1/market/sw-industry/drill/members", {
    level1_code: opts.level1Code,
    level2_code: opts.level2Code,
    level3_code: opts.level3Code,
    ...drillParams(opts.asOf),
  });
}
