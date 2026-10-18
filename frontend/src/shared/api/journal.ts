import { apiGet, apiPost } from "./client";

/**
 * 决策日志（S9）读写面：`/api/v1/journal`。
 *
 * 产品边界（spec §S9.1/§S9.4/§S9.5）**逐条落在类型上**，前端不得越界：
 * - **append-only**：只有「记一笔」与「复盘」两个写操作（`POST /journal`、
 *   `POST /journal/{id}/review`）。接口层不提供 PATCH/PUT/DELETE，故这里**故意**
 *   不导出任何 update/remove 函数——页面也就没有"改一笔"的入口（3 行写下就不能改，
 *   这是 S9 的核心纪律）；
 * - **自动结果是后端算的**：`outcome_pct / bench_pct / excess_pct / invalidation_hit`
 *   全部来自 `evaluation`，前端不重算、也不拿行情自己补；
 * - **归因是用户的**：五枚举必须由用户选（未选不提交，`verdict` 在提交载荷里是
 *   必填而不是可选），`note` 是可选自由文本，原样存原样显示；
 * - **统计样本不足不说话**：`reviewed_n < 5` 时页面只写"样本不足"，不显示占比
 *   （`by_verdict` 的占比在样本少时是噪声，容易被读成"你最常犯的就是这个错"）。
 *
 * 形状口径：`GET /api/v1/journal?status=open|due|reviewed` → `{ items: [...] }`，
 * 每条自带 `status`；前端**按后端给的 `status` 分组**，不按 `due_on` 与本地时钟自己判
 * （时区/交易日口径各算一份必然漂移，且 e2e 会变成"跨天就红"）。
 */

/** 条目状态：`open` 未到期 · `due` 到期待复盘 · `reviewed` 已复盘。 */
export type JournalStatus = "open" | "due" | "reviewed";

/** 归因五枚举（spec §S9.2 的 `check (verdict in (...))`，多一种都写不进去）。 */
export type JournalVerdict =
  | "thesis_wrong"
  | "timing_wrong"
  | "discipline_break"
  | "right_for_wrong_reason"
  | "luck";

/** 失效条件的三种形态：价格下限 / 价格上限 / 只看日期。 */
export type JournalInvalidationKind = "price_below" | "price_above" | "date_only";

/** 归档标签：只用于用户自己分类，**不参与任何计算**（spec §S9.1）。 */
export type JournalKind = "watch" | "buy" | "sell" | "pass" | "hold";

/** 观察期限三选一（spec §S9.2 的 `check (horizon_days in (7,30,90))`）。 */
export type JournalHorizon = 7 | 30 | 90;

/**
 * 失效条件（`invalidation` jsonb 的前端投影）。
 * - `price_below` / `price_above`：`value` 是价格（number）；
 * - `date_only`：`value` 是 `YYYY-MM-DD`（string），到期即视为触发。
 */
export interface JournalInvalidation {
  kind: JournalInvalidationKind;
  value: number | string;
}

/** 快照里的一条事件（S8 探测器的当日命中，最多 3 条）。 */
export interface JournalSnapshotEvent {
  detector: string;
  headline: string;
}

/**
 * 创建时的市场快照（`snapshot` jsonb，spec §S9.3 的字段表；**字段名由契约固定**）。
 *
 * 纪律：任一来源缺数 → 该字段 `null`（不是 0），原因在 `missing_reason` 里。
 * 前端只**读**这份数据：列表行给一行摘要（`snapshot_summary`），详情不重算。
 */
export interface JournalSnapshot {
  price?: number | null;
  pct_chg?: number | null;
  ma20_dist_pct?: number | null;
  sentiment_percentile?: number | null;
  sector_flow_state?: string | null;
  events?: JournalSnapshotEvent[] | null;
  valuation_pct?: number | null;
  /** 缺数原因（字段名 → 原因）；`null`/缺省 = 没有缺数记录。 */
  missing_reason?: Record<string, string> | string | null;
}

/** 自动结果（到期后、复盘前即可展示；全部由后端 `evaluate_entry` 算）。 */
export interface JournalEvaluation {
  outcome_pct: number | null;
  bench_pct: number | null;
  excess_pct: number | null;
  invalidation_hit: boolean | null;
}

/** 一条复盘（append-only，`unique(entry_id)`：一个条目至多一条）。 */
export interface JournalReview extends JournalEvaluation {
  id?: number;
  entry_id?: number;
  reviewed_at?: string;
  verdict: JournalVerdict;
  note?: string | null;
}

/** 一条决策记录（列表项与创建响应同形）。 */
export interface JournalEntry {
  id: number;
  /** `null` = 市场级判断（后端按沪深 300 对比，前端只显示"市场"）。 */
  symbol: string | null;
  kind: JournalKind;
  thesis: string;
  invalidation: JournalInvalidation;
  horizon_days: number;
  snapshot?: JournalSnapshot | null;
  created_at: string;
  due_on: string;
  /** 分组依据（三种状态由后端划分；前端不自己比日期）。 */
  status: JournalStatus;
  /**
   * 列表行的一行快照摘要（后端拼好的人话串）。
   * 缺省时前端退化用 `snapshot` 的几个固定字段拼一行，**不虚构数值**。
   */
  snapshot_summary?: string | null;
  /** 到期后才有；未到期为 `null`。复盘弹窗的只读区就是它。 */
  evaluation?: JournalEvaluation | null;
  /** 已复盘才有。 */
  review?: JournalReview | null;
}

export interface JournalListResponse {
  items: JournalEntry[];
}

/** 「记一笔」的提交载荷：3 个必填（`thesis` / `invalidation` / `horizon_days`）。 */
export interface JournalEntryCreate {
  symbol?: string;
  kind?: JournalKind;
  thesis: string;
  invalidation: JournalInvalidation;
  horizon_days: JournalHorizon;
}

/** 归因提交载荷：`verdict` 必填（未选不得提交，后端缺它 → 422）。 */
export interface JournalReviewPayload {
  verdict: JournalVerdict;
  note?: string;
}

/**
 * 统计卡载荷（spec §S9.4）。
 *
 * - `n` / `reviewed_n`：总条数 / 已复盘条数；
 * - `by_verdict`：五类归因各自的条数（合计 = `reviewed_n`）；
 * - `avg_excess_pct` / `discipline_break_share`：无样本时是 **`null`**（不是 0），
 *   页面显示 `—`；
 * - `by_attribution` 是后端可能一并给出的同一组数的另一个名字，前端**不**依赖它。
 */
export interface JournalStats {
  n: number;
  reviewed_n: number;
  by_verdict?: Partial<Record<JournalVerdict, number>> | null;
  by_attribution?: Partial<Record<JournalVerdict, number>> | null;
  avg_excess_pct: number | null;
  discipline_break_share: number | null;
}

/** 按状态取列表（三种状态各一次请求；页面并行取三份）。 */
export function fetchJournalEntries(status: JournalStatus): Promise<JournalListResponse> {
  return apiGet<JournalListResponse>("/api/v1/journal", { status });
}

/** 记一笔（唯一的新增入口；成功后条目**不可改**）。 */
export function createJournalEntry(payload: JournalEntryCreate): Promise<JournalEntry> {
  return apiPost<JournalEntry>("/api/v1/journal", payload);
}

/** 提交复盘（每个条目只能评一次；重复提交由后端 409 拒绝）。 */
export function reviewJournalEntry(
  id: number,
  payload: JournalReviewPayload
): Promise<JournalReview> {
  return apiPost<JournalReview>(`/api/v1/journal/${id}/review`, payload);
}

/** 统计（页面底部的统计卡；`reviewed_n < 5` 时只显示"样本不足"）。 */
export function fetchJournalStats(): Promise<JournalStats> {
  return apiGet<JournalStats>("/api/v1/journal/stats");
}
