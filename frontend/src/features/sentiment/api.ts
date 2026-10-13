/**
 * `/sentiment` 的取数口：三个受保护读面各一次请求。
 *
 *  1. `GET /api/v1/market/sentiment/composite`（S1.13 新增，契约已冻结）—— hero 的
 *     综合指数值 / 分位 / 参与腿数 / 生效权重；
 *  2. `GET /api/v1/metrics/latest?scope_keys=market`（S1.2 通用读面）—— 9 个模块面板的全部数值；
 *  3. `GET /api/v1/market/sentiment/trends`（S1.8/Task7）—— 后端已算好的三个历史分位。
 *
 * **前端不计算任何分位、不补任何缺值**：本文件只做 snake_case → camelCase 的逐字段搬运，
 * 缺字段一律 `null`（`undefined` 也不许塌成 0）。
 */
import { apiGet } from "@/shared/api/client";

const COMPOSITE_PATH = "/api/v1/market/sentiment/composite";

/** 端点响应（契约字段逐字对应；端点未就绪时整对象不可得，不构造半成品）。 */
interface BackendSentimentComposite {
  as_of?: string | null;
  value?: number | null;
  window?: number | null;
  sample_count?: number | null;
  percentile?: number | null;
  insufficient_history?: boolean | null;
  legs?: string[] | null;
  leg_count?: number | null;
  total_legs?: number | null;
  effective_weight?: number | null;
  min_effective_weight?: number | null;
  caliber_version?: string | null;
}

export interface SentimentComposite {
  asOf: string | null;
  value: number | null;
  /** 分位窗口（交易日数） */
  window: number | null;
  /** 该窗口内的有效样本数 */
  sampleCount: number | null;
  /** 历史分位（0–100）；`null` = 没有"今天的位置"可定位（见下） */
  percentile: number | null;
  /**
   * 有效样本 < 60（后端同一条纪律）。
   *
   * ⚠️ **不**与 `percentile == null` 等价：后端在"当期 value 为 NULL"时也会给
   * `percentile=null` 而 `insufficient_history=false`（没有今日值可分位）。
   * 两者都**不显示分位数字、不画标记**，但文案不同——把"今天没有值"说成"样本不足"
   * 是另一种谎。故这里逐字保留后端布尔值，不在这里做归并。
   */
  insufficientHistory: boolean;
  /** 当日实际参与合成的腿（metric_key） */
  legs: string[];
  legCount: number | null;
  totalLegs: number | null;
  /** 参与腿配置权重之和（= 生效权重之和；分母被重新归一化过） */
  effectiveWeight: number | null;
  /** 低于该值即落 NULL（insufficient_effective_weight） */
  minEffectiveWeight: number | null;
  caliberVersion: string | null;
}

function mapComposite(b: BackendSentimentComposite): SentimentComposite {
  const legs = Array.isArray(b.legs) ? b.legs.filter((k): k is string => typeof k === "string") : [];
  return {
    asOf: b.as_of ?? null,
    value: b.value ?? null,
    window: b.window ?? null,
    sampleCount: b.sample_count ?? null,
    percentile: b.percentile ?? null,
    // `insufficient_history` 与 `percentile=null` 在后端**不是**同一件事（当期 value 为 NULL
    // 也会给 percentile=null），故逐字保留，不在映射层归并（见接口注释）
    insufficientHistory: Boolean(b.insufficient_history),
    legs,
    legCount: b.leg_count ?? (legs.length > 0 ? legs.length : null),
    totalLegs: b.total_legs ?? null,
    effectiveWeight: b.effective_weight ?? null,
    minEffectiveWeight: b.min_effective_weight ?? null,
    caliberVersion: b.caliber_version ?? null,
  };
}

/** 综合情绪指数（S1.13）。端点未落地时抛 `ApiError(404)`，由 hero 渲染"暂不可得"。 */
export function fetchSentimentComposite(): Promise<SentimentComposite> {
  return apiGet<BackendSentimentComposite>(COMPOSITE_PATH).then(mapComposite);
}
