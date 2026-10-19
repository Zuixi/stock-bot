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

/**
 * 仓位粗档（`position_size`，spec §S9.7.1）：`none` = 只观察不下单，创建时选定。
 *
 * 权重口径 `size_weight = {none:0, light:0.5, normal:1, heavy:2}` 由后端算（`weighted_*`），
 * 前端只用它写清"只观察不计收益"，不自己乘。
 */
export type JournalPositionSize = "none" | "light" | "normal" | "heavy";

/**
 * 赌的是什么（`bet_type`，spec §S9.7.3）：基准必须匹配赌注类型，创建时选定、事后不可改。
 *
 * - `direction`（默认）：赌这个行业/标的会涨 → 所属申万一级**成分股等权平均收益**；
 * - `relative`：赌这只**跑赢同行** → 同行业等权平均收益；
 * - `market`：市场级判断（无标的）→ 沪深300。
 *
 * Phase A 的后端字段尚未上线时，请求体省略它即可（后端按默认 `direction` 处理）。
 */
export type JournalBetType = "direction" | "relative" | "market";

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
 * 同类历史里的"你自己"一层（spec §S9.6.2）：只数**本人已复盘**的条目。
 *
 * `n < 5` 时 `insufficient = true`：页面只许显示"样本不足"，不给比例（§S9.6.5）。
 * `right` = 没有被判成错（`wrong` 由三类归因合起来算）；`right_for_wrong_reason` /
 * `luck` **单独计数**——"赚了但理由错"既不算对也不算错。
 */
export interface JournalPeerHistorySelf {
  n: number;
  right: number;
  wrong: number;
  /** 错在哪一类的构成：`verdict → 次数`（spec §S9.6.5 要求必须显示）。 */
  wrong_by?: Record<string, number> | null;
  right_for_wrong_reason?: number | null;
  luck?: number | null;
  insufficient?: boolean | null;
}

/** 同类历史里的"全市场同桶"一层（spec §S9.6.2；**不含任何他人日志**，只用行情统计）。 */
export interface JournalPeerHistoryMarket {
  /** 产物统计截止到哪天（PIT：必须早于快照日）。 */
  as_of: string;
  n: number;
  t20_n?: number | null;
  t5_excess_med: number | null;
  t20_excess_med: number | null;
  min_sample?: number | null;
  /** `n < 30` → 不给数字（§S9.6.2）。 */
  insufficient?: boolean | null;
}

/**
 * 同类历史（`snapshot.peer_history`，spec §S9.6.4）：**创建那一刻**算一次并落库。
 *
 * 三层纪律：`self` 与 `market` **分开呈现**（不得合并成一个"胜率"）；
 * `bucket` 是当时的分桶（情绪|板块|MA20 三维，`unknown` 单列）；
 * `notes` 由后端给（"只描述过去，不预测结果" + 降级原因），页面**原样**展示。
 */
export interface JournalPeerHistory {
  version: string;
  computed_at: string;
  bucket: { sentiment: string; sector: string; ma20: string } | null;
  self: {
    same_symbol?: JournalPeerHistorySelf | null;
    same_bucket?: JournalPeerHistorySelf | null;
    min_sample?: number | null;
  } | null;
  market: JournalPeerHistoryMarket | null;
  notes: string[];
}

/**
 * 创建时固化的基准（`snapshot.bench_spec`，spec §S9.7.3；**创建时写死、事后不可改**）。
 *
 * 全部字段可选：后端 Phase A 未上线时快照里就没有这一块，页面必须优雅退化
 * （不显示基准说明，而不是显示 "undefined"）。
 */
export interface JournalBenchSpec {
  bet_type?: JournalBetType | null;
  /** 基准种类（如 `industry_equal_weight`），前端只展示不解释。 */
  kind?: string | null;
  industry_code?: string | null;
  industry_name?: string | null;
  /** 无行业归属 → 基准退化为沪深300 时为 `true`。 */
  fallback?: boolean | null;
}

/** 所属申万一级的当日状态（`snapshot.sector_flow_state`，spec §S9.3）。 */
export interface JournalSectorFlowState {
  code: string;
  name: string;
  /** 当日涨跌幅（%）：同类历史的分桶维度之一（spec §S9.6.1）。 */
  pct: number;
  /** 全市场 L1 分位（%）；老快照可能缺省。 */
  rank?: number | null;
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
  /** 所属申万一级当日状态（后端下发**对象**；老数据/缺数时为 `null`）。 */
  sector_flow_state?: JournalSectorFlowState | null;
  events?: JournalSnapshotEvent[] | null;
  valuation_pct?: number | null;
  /** 缺数原因（字段名 → 原因）；`null`/缺省 = 没有缺数记录。 */
  missing_reason?: Record<string, string> | string | null;
  /** 创建时固化的基准（spec §S9.7.3）；Phase A 未上线时缺省。 */
  bench_spec?: JournalBenchSpec | null;
  /** 基准口径的人话说明（创建时也写一份）；Phase A 未上线时缺省。 */
  bench_basis_note?: string | null;
}

/**
 * 自动结果（到期后、复盘前即可展示；全部由后端 `evaluate_entry` 算）。
 *
 * 净口径四字段（spec §S9.7.2/§S9.7.3）都是**可选**的：后端 Phase A 未上线时只有毛字段，
 * 页面回退毛口径并明确标注"毛"，绝不把毛当净、也绝不补 0。
 */
export interface JournalEvaluation {
  outcome_pct: number | null;
  bench_pct: number | null;
  excess_pct: number | null;
  invalidation_hit: boolean | null;
  /** 净收益 = 毛收益 − 12bp 往返成本（spec §S9.7.2）。 */
  net_outcome_pct?: number | null;
  /** 净超额 = 净收益 − 基准（spec §S9.7.2）。 */
  net_excess_pct?: number | null;
  /** 基准的人话说明（spec §S9.7.3）。 */
  bench_basis_note?: string | null;
  /** 无行业归属、基准退化为沪深300 时为 `true`。 */
  bench_fallback?: boolean | null;

  // ── 路径分析（spec §S9.7.4；Phase B 后端未上线时全部缺省） ──────────────
  /** 入场到到期期间的最大不利偏移（%）。 */
  mae_pct?: number | null;
  /** 首次触达失效条件的日期（`YYYY-MM-DD`）；未触达 = `null`。 */
  first_touch_invalidation?: string | null;
  /** 先触发失效条件、到期反而盈利。复盘弹窗据此给"纪律问题"提示（不强制选择）。 */
  touched_then_reversed?: boolean | null;
  /** 首次触达失效条件距入场交易日数；未触达 = `null`。 */
  days_to_invalidation?: number | null;
  /** 失效条件是否可观测（`false` = 该条不可复盘，spec §S9.7.8 的闸）。 */
  invalidation_observable?: boolean | null;

  // ── 财报窗口（`.superpowers/sdd/journal-deep/contract.md` §1） ─────────────
  /**
   * 该股在 **(快照日, due_on]** 窗口内是否有财报披露日。
   *
   * 三态而不是布尔：`true` 窗口内有财报（结果可能被事件主导）/ `false` 有财报数据但
   * 不在窗口内 / `null` = 该股没有任何财报记录（缺数据，不猜）。前端只在 `=== true`
   * 时给提示，`null` 不渲染（缺数据不是"没有财报"）。
   */
  earnings_in_window?: boolean | null;
  /** 财报窗口的人话说明（后端下发，**原样**展示；无则空串）。 */
  earnings_note?: string | null;

  // ── 解禁窗口（spec §S11.3；与财报窗口**同构**） ─────────────────────────
  /**
   * 判断窗口 `(快照日, due_on]` 内有没有"占流通 ≥3%"的解禁（`share_floats` 的
   * `float_date` 落在窗口内）。
   *
   * 三态而不是布尔：`true` 窗口内有解禁（结果可能被解禁冲击主导）/ `false` 有解禁记录
   * 但窗口内没有 ≥3% 的 / `null` = 该股**没有任何解禁记录**（缺数据，不猜）。前端只在
   * `=== true` 时给提示，`false` / `null` 都不渲染——缺数据不是"不会解禁"。
   *
   * 注意用词：解禁 = 可流通，与减持是两件事，页面文案一律用后端下发的 `unlock_note`
   * （「解禁」），不由前端改写。
   */
  unlock_in_window?: boolean | null;
  /** 解禁窗口的人话说明（后端下发，**原样**展示；无则空串）。 */
  unlock_note?: string | null;
  /**
   * 缺数原因（字段名 → 原因；`{}`/缺省 = 没有缺数记录）。
   *
   * 注意它**不只是**"结果算不出来"：后端的 `evaluate_entry` 把路径/财报/解禁这类**降级**也记在
   * 同一个字典里（市场级判断恒有 `mae_pct: market_level_no_symbol`、没有财报数据的个股有
   * `earnings: no_financial_reports`、没有解禁数据的个股有 `unlock: no_share_float_rows`）。
   * 判断"结果未就绪"只看结果键（`metrics.ts` 的 `hasUnreadyMissingReason`），否则绝大多数
   * 真实条目会永远点不了「复盘」。
   */
  missing_reason?: Record<string, string> | string | null;
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
  /** 创建时选定的赌注类型（spec §S9.7.3）；Phase A 未上线时缺省。 */
  bet_type?: JournalBetType | null;
  /**
   * 仓位粗档（spec §S9.7.1）；缺省时列表**不猜**「只观察」（那会把"没记录"说成"没下单"）。
   */
  position_size?: JournalPositionSize | null;
  /**
   * 事前信心 1–5（spec §S9.7.5）；`null`/缺省 = 不参与校准。
   * 只用于统计分档，前端不拿它改写任何结果。
   */
  confidence?: number | null;
  /**
   * 是否可复盘（spec §S9.7.8）：`false` = 失效条件不可观测 → **不提供**复盘入口。
   * 缺省按可复盘处理（Phase B 后端未上线时旧条目照旧能复盘）。
   */
  reviewable?: boolean | null;
  /**
   * 同类历史（spec §S9.6.4）：创建时写进快照、原样下发；老行（s9.r1/r2）没有它 → `null`。
   * 页面缺省时**不渲染**这张卡（不补 0、不虚构"你以前 0 对 0 错"）。
   */
  peer_history?: JournalPeerHistory | null;
}

export interface JournalListResponse {
  items: JournalEntry[];
}

/** 「记一笔」的提交载荷：3 个必填（`thesis` / `invalidation` / `horizon_days`）。 */
export interface JournalEntryCreate {
  symbol?: string;
  kind?: JournalKind;
  /** 赌注类型（spec §S9.7.3，默认 `direction`）；后端 Phase A 未上线时多传它的后果见契约。 */
  bet_type?: JournalBetType;
  /** 仓位粗档（spec §S9.7.1，默认 `none` = 只观察）；Phase B 后端未上线时后端会忽略。 */
  position_size?: JournalPositionSize;
  /** 事前信心 1–5（spec §S9.7.5）；**省略 = 不参与校准**（不是 0，也不是 3）。 */
  confidence?: number;
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
 * 一个比例的 Wilson 95% 区间（spec §S9.7.7）：`4/6（95% 区间 30%–90%）`。
 * `ci_low` / `ci_high` 是 0–1 的小数；`n` 是**该比例的**样本数（不一定等于 `reviewed_n`）。
 */
export interface JournalShareCI {
  n: number;
  successes: number;
  ci_low: number;
  ci_high: number;
}

/**
 * 统计卡载荷（spec §S9.4，S9.7 追加项见下）。
 *
 * - `n` / `reviewed_n`：总条数 / 已复盘条数；
 * - `by_verdict`：五类归因各自的条数（合计 = `reviewed_n`）；
 * - `avg_excess_pct` / `discipline_break_share`：无样本时是 **`null`**（不是 0），
 *   页面显示 `—`；
 * - `by_attribution` 是后端可能一并给出的同一组数的另一个名字，前端**不**依赖它。
 *
 * S9.7 追加（全部**可选**：Phase A 未上线时页面按毛口径/旧形态退化）：
 * - `by_kind`：五种归档标签的条数（合计 = `n`；`pass` = 我决定不动，只计入判断）；
 * - `avg_net_excess_pct`：净口径平均超额（默认展示它，毛字段只做退化兜底）；
 * - `share_ci`：比例一律带 Wilson 区间（键见 `JournalShareCIKey`）；
 * - `net_cost_bps`：净口径用的往返成本（展示"毛/净（成本 Nbp）"）；
 * - `multiple_comparison_note`：固定标注，前端**原样**展示，不改写；
 * - `sample_sufficient` / `min_reviewed_for_share`：后端的样本判定，前端以 `reviewed_n < 5`
 *   为准（两者不一致时仍守住"不显示占比"这条底线）。
 */
export type JournalShareCIKey = "discipline_break" | "win";

/**
 * 校准曲线的一档（`stats.calibration.bins[]`，spec §S9.7.5）。
 *
 * - `confidence`：1–5 的信心档；
 * - `p`：该档的**固定**先验概率（`{1:0.2, 2:0.35, 3:0.5, 4:0.65, 5:0.8}`，后端算，前端不重算）；
 * - `hit_rate`：该档实际净超额 > 0 的比例（0–1）；
 * - `sufficient`：后端判定该档样本是否够（展示层仍以 `n >= 10` 为准，两者取严）。
 */
export interface JournalCalibrationBin {
  confidence: number;
  p?: number | null;
  n: number;
  hit_rate?: number | null;
  sufficient?: boolean | null;
}

/**
 * 校准卡（spec §S9.7.5）：`n` = 已复盘且填了信心的总样本；样本不足时后端给 `null`。
 * 全部字段可选——Phase B 后端未上线时整块缺省，页面**不显示**校准卡（不补 0、不虚构）。
 */
export interface JournalCalibration {
  n: number;
  bins?: JournalCalibrationBin[] | null;
  /** `mean((p - I[net_excess>0])**2)`；`null`/缺省时只不显示这一个数。 */
  brier?: number | null;
}

/**
 * 一类归档标签的净超额中位（`stats.by_kind_median[kind]`，契约 §3）。
 *
 * `count` 是中位数的样本数（已复盘条数），与 `by_kind[kind]` 的"全部条数"不是同一件事。
 * `median_net_excess_pct` **可为 `null`**（该类没有可算净超额的复盘）→ 页面显示 `—`，
 * **不是** 0：0 是"中位净超额正好是 0"，是另一种假话。
 */
export interface JournalKindMedian {
  count?: number | null;
  median_net_excess_pct?: number | null;
}

/**
 * 归因自洽率（`stats.consistency`，契约 §2）。
 *
 * 自洽 = "我归类的原因"与"净超额的符号"方向一致（`discipline_break` 恒自洽：纪律问题
 * 与盈亏独立）。`n` = 已复盘且净超额可算的条数；`n < 5` 时 `share` / `ci_low` /
 * `ci_high` 为 `null`（只给 `n`）——此时页面只写"样本不足"，不给任何百分比。
 * `note` 由后端下发（"不自洽不代表你说谎…"），页面**原样**展示，不改写。
 */
export interface JournalConsistency {
  n: number;
  consistent: number;
  share?: number | null;
  ci_low?: number | null;
  ci_high?: number | null;
  note?: string | null;
}

export interface JournalStats {
  n: number;
  reviewed_n: number;
  by_verdict?: Partial<Record<JournalVerdict, number>> | null;
  by_attribution?: Partial<Record<JournalVerdict, number>> | null;
  avg_excess_pct: number | null;
  discipline_break_share: number | null;
  /** 五种归档标签各自条数（spec §S9.7.6）；Phase A 未上线时缺省。 */
  by_kind?: Partial<Record<JournalKind, number>> | null;
  /** 净口径平均超额（spec §S9.7.2）；缺省时回退 `avg_excess_pct`。 */
  avg_net_excess_pct?: number | null;
  /** 比例 + Wilson 区间（spec §S9.7.7）；Phase A 未上线时缺省。 */
  share_ci?: Partial<Record<JournalShareCIKey, JournalShareCI>> | null;
  /** 净口径的往返成本（bp）；缺省按契约常量 12 展示。 */
  net_cost_bps?: number | null;
  /** 「未做多重比较校正」固定标注（spec §S9.7.7），原样展示。 */
  multiple_comparison_note?: string | null;
  /** 后端下发的排除口径（`pass` / 只观察 各自的去处），前端原样展示、不改写 */
  exclusion_note?: string | null;
  sample_sufficient?: boolean | null;
  min_reviewed_for_share?: number | null;

  // ── Phase B 追加（全部可选；缺省时页面按旧形态退化） ────────────────────
  /** 四种仓位粗档各自的条数（spec §S9.7.1；`none` 计入判断、不计入收益）。 */
  by_size?: Partial<Record<JournalPositionSize, number>> | null;
  /** 按 `size_weight` 加权的平均超额（`none` 排除，spec §S9.7.1）；缺省时**不显示**，不补 0。 */
  avg_weighted_excess_pct?: number | null;
  /** 校准曲线（spec §S9.7.5）；样本不足时为 `null`，原因在 `calibration_note`。 */
  calibration?: JournalCalibration | null;
  /** 校准样本不足的原因（原样展示，前端不改写）。 */
  calibration_note?: string | null;
  /** 导出端点（spec §S9.7.9），前端直接用；缺省时拼固定路径。 */
  export_hint?: string | null;

  // ── 深度优化追加（契约 §2/§3；全部可选，缺省时整块不渲染） ──────────────
  /** 每类归档标签的净超额中位（`{count, median_net_excess_pct}`）；缺省时只显示条数。 */
  by_kind_median?: Partial<Record<JournalKind, JournalKindMedian>> | null;
  /** 归因自洽率（`{n, consistent, share, ci_low, ci_high, note}`）；缺省时整行不渲染。 */
  consistency?: JournalConsistency | null;
}

/** 导出端点的退化路径（后端未给 `export_hint` 时用它）。 */
export const JOURNAL_EXPORT_FALLBACK = "/api/v1/journal/export?format=csv";

/** 取导出链接：**优先**后端给的 `export_hint`，缺省用契约里的固定端点。 */
export function journalExportUrl(hint?: string | null): string {
  const trimmed = (hint ?? "").trim();
  return trimmed.length > 0 ? trimmed : JOURNAL_EXPORT_FALLBACK;
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

/**
 * 到期提醒的聚合计数（`GET /api/v1/journal/summary`，spec §S12.2）→ `{open, due, reviewed}`。
 *
 * 三个数都是**事实**（"待复盘 N 条"），不是未读游标：不做邮件/推送/未读态（spec §S12.2
 * 的「不做」），故前端也**不**为它维护任何本地状态。
 *
 * `null` 与 `0` 严格区分：`0` 是"确实没有到期条目"，`null`/缺字段是"没取到它"。
 * 两者在角标上都不渲染（见 `summaryCount` 的调用方），但**不能**在数据层就把前者写成后者
 * ——一旦把缺字段兜成 0，页面上再想区分「今天没事」与「今天不知道」就没有依据了。
 */
export interface JournalSummary {
  open: number;
  due: number;
  reviewed: number;
}

/**
 * 取 summary（角标与 triage 顶部到期提示共用这一个读面）。
 *
 * `skipAuth`：它是**装饰性**读面——失败只让角标/提示消失，不该把用户踢下线、
 * 也不该触发全局 `auth:unauthorized` 清态（同 `fetchRegime` 的口径）。主线读面（列表/
 * 统计）仍然是普通 `apiGet`，401 的全局语义一个字不动。
 */
export function fetchJournalSummary(): Promise<JournalSummary> {
  return apiGet<JournalSummary>("/api/v1/journal/summary", undefined, { skipAuth: true });
}

/**
 * 读 summary 里的一个计数：**缺字段/非法值 → `null`（不是 0）**。
 *
 * 后端并行开发中时这个端点可能还没上线（404/500）或只回了形状不全的 JSON；
 * 这里把"拿不到"如实返回成 `null`，让调用方决定"不显示"，而不是替它写一个 0。
 */
export function summaryCount(
  summary: JournalSummary | null | undefined,
  key: keyof JournalSummary
): number | null {
  const value = summary?.[key];
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  return Math.trunc(value);
}
