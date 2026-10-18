import type {
  JournalCalibration,
  JournalCalibrationBin,
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
