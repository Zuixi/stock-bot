import type {
  JournalCalibration,
  JournalCalibrationBin,
  JournalConsistency,
  JournalEvaluation,
  JournalShareCI,
} from "@/shared/api/journal";

/**
 * 决策日志的**口径与格式化单一入口**（S9.7.2 净成本 / S9.7.3 基准 / S9.7.5 校准 /
 * S9.7.7 区间）。
 *
 * 为什么单独一个文件：同一个净超额在列表行与复盘弹窗两处上屏，同一句"毛/净（成本 12bp）"
 * 两处各写一份，必然漂移成"列表按净、弹窗按毛"这种最难查的错。故：
 * - 百分比怎么格式化、口径怎么标、区间怎么写，只有这一份实现；
 * - 净字段（`net_outcome_pct` / `net_excess_pct`）**可能不存在**（后端 Phase A 未上线），
 *   回退毛口径时必须明确标注是毛，绝不把毛当净。
 */

/** 往返成本（spec §S9.7.2：`COST_ROUND_TRIP_BPS = 12` = 佣金 5 + 印花 5 + 过户/滑点 2）。 */
export const COST_ROUND_TRIP_BPS = 12;

/** 比例展示的样本下限（spec §S9.6/§S9.7.7：`n < 5` 不给比例）。 */
export const MIN_SHARE_SAMPLE = 5;

/** 百分比口径：保留符号（可以为负），取不到时 `—` 而不是 0。 */
export function formatSignedPct(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${value > 0 ? "+" : ""}${value.toFixed(2)}%`;
}

/** 占比（后端给 0–1 的小数）：`0.375` → `37.5%`；`null` → `—`。 */
export function formatShare(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${(value * 100).toFixed(1)}%`;
}

/** 口径标签：净字段可用时标「毛/净（成本 Nbp）」，回退毛时标"净字段缺失"。 */
export function netBasisLabel(bps: number | null | undefined): string {
  const cost = bps === null || bps === undefined ? COST_ROUND_TRIP_BPS : bps;
  return `毛/净（成本 ${cost}bp）`;
}

/** 回退毛口径时的显式标注（不能只把数字换个来源、却仍写着"净"）。 */
export const GROSS_FALLBACK_LABEL = "净字段缺失，显示毛口径";

/** 收益的两种口径：`net` 净 / `gross` 毛（净字段缺失）/ `none` 两者都没有。 */
export type MetricBasis = "net" | "gross" | "none";

export interface BasisValue {
  value: number | null | undefined;
  basis: MetricBasis;
}

/** 默认净口径：净字段可用 → 用它；否则回退毛字段并标 `gross`。 */
export function pickBasisValue(
  gross: number | null | undefined,
  net: number | null | undefined
): BasisValue {
  if (net !== null && net !== undefined && !Number.isNaN(net)) return { value: net, basis: "net" };
  if (gross !== null && gross !== undefined && !Number.isNaN(gross)) {
    return { value: gross, basis: "gross" };
  }
  return { value: null, basis: "none" };
}

/** 自动结果里"结果/超额"各自的默认口径（`evaluation` 可能为 `null`）。 */
export function netOutcomeOf(evaluation: JournalEvaluation | null | undefined): BasisValue {
  return pickBasisValue(evaluation?.outcome_pct, evaluation?.net_outcome_pct);
}

export function netExcessOf(evaluation: JournalEvaluation | null | undefined): BasisValue {
  return pickBasisValue(evaluation?.excess_pct, evaluation?.net_excess_pct);
}

/**
 * 比例 + Wilson 95% 区间（spec §S9.7.7）：`{n:6, successes:4, ci_low:.30, ci_high:.90}`
 * → `4/6（95% 区间 30%–90%）`。
 *
 * 返回 `null` 表示**不能给比例**（缺区间数据或 `n < 5`）——调用方据此退回"样本不足"
 * 或旧形态的百分比，绝不自己拍一个点估计上去。
 */
export function formatShareWithCi(ci: JournalShareCI | null | undefined): string | null {
  if (!ci || !Number.isFinite(ci.n) || ci.n < MIN_SHARE_SAMPLE) return null;
  const low = Math.round(ci.ci_low * 100);
  const high = Math.round(ci.ci_high * 100);
  return `${ci.successes}/${ci.n}（95% 区间 ${low}%–${high}%）`;
}

/**
 * 统计卡里一个比例的展示：有区间 → `4/6（95% 区间 30%–90%）`；
 * 有区间但 `n < 5` → `样本不足`；没有区间对象（Phase A 未上线）→ 退化为旧百分比。
 */
export function formatShareOrSample(
  ci: JournalShareCI | null | undefined,
  fallbackShare: number | null | undefined
): string {
  if (ci) return formatShareWithCi(ci) ?? "样本不足";
  return formatShare(fallbackShare);
}

// ── 校准（spec §S9.7.5） ────────────────────────────────────────────────

/** 每档显示命中率的样本下限（spec §S9.7.5：每档 `n >= 10` 才显示）。 */
export const MIN_CALIBRATION_BIN_SAMPLE = 10;

/** 校准曲线的总样本下限（spec §S9.7.5：总 `n < 20` 只显示 `calibration_note`）。 */
export const MIN_CALIBRATION_SAMPLE = 20;

/**
 * 总样本够不够给校准曲线：`calibration` 缺省（Phase B 后端未上线）或 `n < 20` 都按"不够"。
 * 不够时页面**只显示后端给的 `calibration_note`**，不画半条曲线。
 */
export function hasCalibrationCurve(
  calibration: JournalCalibration | null | undefined
): calibration is JournalCalibration {
  return (
    !!calibration &&
    Number.isFinite(calibration.n) &&
    calibration.n >= MIN_CALIBRATION_SAMPLE
  );
}

/**
 * 一档的"实际命中率"：`n >= 10`（且后端没判该档样本不足）才给比例，
 * 否则写"样本不足（n=4）"——**不给**点估计，也不补 0。
 */
export function formatCalibrationRate(bin: JournalCalibrationBin): string {
  const n = Number.isFinite(bin.n) ? bin.n : 0;
  if (n < MIN_CALIBRATION_BIN_SAMPLE || bin.sufficient === false) {
    return `样本不足（n=${n}）`;
  }
  return formatShare(bin.hit_rate);
}

/** `brier` 的展示：`0.231`（后端已算好，前端只保留三位小数）；缺省 → `null`（不显示）。 */
export function formatBrier(brier: number | null | undefined): string | null {
  if (brier === null || brier === undefined || Number.isNaN(brier)) return null;
  return brier.toFixed(3);
}

// ── 归因自洽率（契约 §2） ───────────────────────────────────────────────

/**
 * 「归因自洽 N/M（95% 区间 x%–y%）」（契约 §2）。
 *
 * 三条纪律：
 * - `n < 5` → **只**写「样本不足」，不给比例也不给区间（沿用 S9.6/S9.7.7）；
 * - `n >= 5` 但后端没给区间（旧响应）→ 只写 `N/M`，**绝不**单独给点估计百分比；
 * - 整个 `consistency` 缺省（后端未上线）→ 返回 `null`，调用方整行不渲染（不补 0）。
 */
export function formatConsistency(
  consistency: JournalConsistency | null | undefined
): string | null {
  if (!consistency || !Number.isFinite(consistency.n)) return null;
  const n = consistency.n;
  const consistent = Number.isFinite(consistency.consistent) ? consistency.consistent : 0;
  if (n < MIN_SHARE_SAMPLE) return "样本不足";
  const { ci_low: low, ci_high: high } = consistency;
  if (
    low === null ||
    low === undefined ||
    high === null ||
    high === undefined ||
    !Number.isFinite(low) ||
    !Number.isFinite(high)
  ) {
    // 没有区间就不给百分比：S9.7.7 要求点估计永远和区间一起出现。
    return `${consistent}/${n}`;
  }
  return `${consistent}/${n}（95% 区间 ${Math.round(low * 100)}%–${Math.round(high * 100)}%）`;
}

// ── 结果是否就绪（契约 §3：未就绪不给复盘入口） ─────────────────────────

/**
 * `missing_reason` 里**出现就说明结果本身算不出来**的键（缺行情/基准）。
 *
 * 口径为什么是"结果键"而不是"非空就拦"：后端的 `evaluate_entry` 会把**路径/财报**
 * 这类**降级**也写进同一个 `missing_reason`（如市场级判断恒有 `mae_pct:
 * market_level_no_symbol`、没有财报数据的个股有 `earnings: no_financial_reports`）。
 * 这些降级不影响 `outcome_pct / bench_pct / invalidation_hit`，后端复盘端点也照收；
 * 若"字典非空即拦"，绝大多数真实条目都会永远点不了「复盘」——那不是契约要的
 * "结果未就绪（缺行情/基准）"。故只认结果键（与后端 422 判据同一组）。
 */
export const RESULT_CRITICAL_MISSING_KEYS: readonly string[] = [
  "outcome_pct",
  "bench_pct",
  "excess_pct",
  "invalidation_hit",
  "net_outcome_pct",
  "net_excess_pct",
];

/**
 * `missing_reason` 里是否记录了**结果本身**的缺数原因（缺行情/基准）。
 *
 * 接受 `{字段: 原因}` 对象（后端真实形态）与字符串（历史/退化形态）：字符串无法分辨
 * 是哪个字段缺，按"结果缺数"处理（宁可先禁用，也不给一个之后必然 422 的入口）。
 * 空对象、`null`、`undefined`、纯空白串都算"没有缺数记录"。
 */
export function hasUnreadyMissingReason(
  reason: JournalEvaluation["missing_reason"] | null | undefined
): boolean {
  if (reason === null || reason === undefined) return false;
  if (typeof reason === "string") return reason.trim().length > 0;
  return RESULT_CRITICAL_MISSING_KEYS.some((key) => {
    const value = (reason as Record<string, string>)[key];
    return value !== null && value !== undefined && String(value).trim().length > 0;
  });
}

/**
 * 这次结果是否**够格**让用户复盘（契约 §3）：
 *
 * - `evaluation` 为空 → 未就绪（到期了但还没算出结果）；
 * - 结果三要素（`outcome_pct` / `bench_pct` / `invalidation_hit`）任一为 `null` → 未就绪；
 * - `missing_reason` 里记录了这三者（及净口径）的缺数原因 → 未就绪（缺行情/基准）。
 *
 * 前两条与后端 `POST /journal/{id}/review` 的拒绝条件一致（`EvaluationNotReady`），
 * 前端先禁用入口，而不是让用户选完归因再吃一个 422。第三条是同一件事的另一份证据
 * （结果字段被下游清空、但原因还留着时也不放行）。
 *
 * 注意：`0` 是**就绪**的值（结果正好 0%），只有 `null`/`undefined` 才是未就绪；
 * 路径/财报类降级（`mae_pct` / `earnings` 等）**不**算未就绪——后端照样接受复盘。
 */
export function isEvaluationReady(
  evaluation: JournalEvaluation | null | undefined
): boolean {
  if (!evaluation) return false;
  const { outcome_pct: outcome, bench_pct: bench, invalidation_hit: hit } = evaluation;
  if (!isFiniteNumber(outcome) || !isFiniteNumber(bench) || typeof hit !== "boolean") {
    return false;
  }
  return !hasUnreadyMissingReason(evaluation.missing_reason);
}

/** 有限数字判定（`null`/`undefined`/`NaN` 都不算）。 */
function isFiniteNumber(value: number | null | undefined): boolean {
  return typeof value === "number" && Number.isFinite(value);
}
