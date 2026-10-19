/**
 * `/sentiment` 的取数口：三个受保护读面各一次请求。
 *
 *  1. `GET /api/v1/market/sentiment/composite`（S1.13 新增，契约已冻结）—— hero 的
 *     综合指数值 / 分位 / 参与成分指标数 / 生效权重；
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
  expected_as_of?: string | null;
  lag_trading_days?: number | null;
  stale?: boolean | null;
  provisional?: boolean | null;
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
  /** 当日实际参与合成的成分指标（metric_key；契约字段名仍为 `legs`） */
  legs: string[];
  legCount: number | null;
  totalLegs: number | null;
  /** 参与成分指标配置权重之和（= 生效权重之和；分母被重新归一化过） */
  effectiveWeight: number | null;
  /** 低于该值即落 NULL（insufficient_effective_weight） */
  minEffectiveWeight: number | null;
  caliberVersion: string | null;
  /**
   * "现在应该有的最新完整交易日"（后端**本地**判定，不外呼）。
   *
   * `null` = 后端判不出（此时 `lagTradingDays`/`stale` 同为 `null`）。
   */
  expectedAsOf: string | null;
  /**
   * `asOf` 落后 `expectedAsOf` 几个**交易日**。
   *
   * ⚠️ 三态，不是布尔：`null` = **无法判定**，`0` = 追平。两者都不显示"滞后"徽标，
   * 但语义完全不同 —— 故这里不把 `null` 归并成 `0`（同 `insufficientHistory` 的纪律）。
   */
  lagTradingDays: number | null;
  /**
   * 是否落后：`true` 落后 / `false` 追平 / `null` 无法判定。
   *
   * ⚠️ 逐字保留后端三态，**不**在这里 `Boolean(...)` 或 `?? false`：把"判不出"
   * 显示成"新鲜"就是在数据可能过期时撒谎。
   */
  stale: boolean | null;
  /**
   * 本期读数是否**临时值**（会被上游披露重算改写）：`true` / `false` / `null`。
   *
   * - `true` = 该行的值**缺了 T+1 披露的成分指标**（交易所融资融券两条），且仍落在收敛器的
   *   10 交易日改写窗口内 ⇒ **这个数字以后会被重算改写**；
   * - `false` = 终值（不会再改）；
   * - `null` = **判不出**（缺参与集登记或缺交易日历）。
   *
   * ⚠️ 三态，不是布尔：`null` **不得**被当作 `false` —— 那等于把"不知道"说成"是终值"，
   * 会让读者放心引用一个可能被改写的数字。故这里逐字保留后端三态，**不**做 `Boolean(...)`
   * 或 `?? false` 归并（同 `stale` / `lagTradingDays` 的纪律）。
   */
  provisional: boolean | null;
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
    expectedAsOf: b.expected_as_of ?? null,
    lagTradingDays: b.lag_trading_days ?? null,
    // 三态逐字搬运：`?? null` 只处理"字段缺失"，**不**把 null 塌成 false
    stale: b.stale ?? null,
    // 同上：字段缺失（老后端不下发）与后端明说"判不出"都落 `null`，都不声称是终值
    provisional: b.provisional ?? null,
  };
}

/** 综合情绪指数（S1.13）。端点未落地时抛 `ApiError(404)`，由 hero 渲染"暂不可得"。 */
export function fetchSentimentComposite(): Promise<SentimentComposite> {
  return apiGet<BackendSentimentComposite>(COMPOSITE_PATH).then(mapComposite);
}
