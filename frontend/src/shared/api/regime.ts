import { apiGet } from "./client";

/**
 * 市场状态机读面（S10）：`GET /api/v1/market/regime`（spec §S10.3，登录即可）。
 *
 * 三条纪律（与后端 `app/schemas/market_regime.py` 的注释同源）：
 *
 * 1. **只陈述已发生的事实**：`state` = 当前判定出的状态，`duration_days` = 已经持续几天，
 *    `forward` = 历史上**处于同一状态之后**的后续 N 个交易日收益分布。这里没有任何
 *    "接下来会怎样"的字段——所以页面上也不许出现那类措辞（spec §S10.5）。
 * 2. **`state` 为 `null` 是合法降级**（产物未生成/无法解析/落后），不是"中性"：
 *    后端不 500，而是给 `state=null` + `notes` 里的原因。页面遇到空 `state` 时
 *    **整行不渲染**（一行字的读面不值得为它留一个"未知"占位）。
 * 3. **`n < 30` 不给分布**：此时该 horizon 的 `median_pct` / `p05_pct` 是 `null`，
 *    只有 `n`。页面必须写"样本不足"，绝不能把 `null` 补成 0（"没有分布"≠"中位是 0%"）。
 *
 * `forward` 的键（`"5d"` / `"20d"`）**由后端生成**、前端不拼：`5d` / `20d` 是数字开头的
 * 键，在 Python 里做不了字段名，故后端的 `forward` 是 `dict[str, ...]`。前端只在
 * 组件里按固定 key 取，取不到就按"没有这一档"处理，不做键名映射表。
 */

/** 五态的固定次序（spec §S10.2 的判定表次序；转移概率按它逐行排，不按概率大小重排）。 */
export const REGIME_STATE_ORDER: readonly string[] = ["冰点", "修复", "亢奋", "退潮", "中性"];

/** 同态后续一档（`forward["5d"]` / `forward["20d"]`）的前端形状。 */
export interface RegimeForwardBucket {
  /** 有完整 N 日后续窗口的入场日数；`null` = 后端没给（不当作 0）。 */
  n: number | null;
  /** 中位收益（%）；`null` = 样本不足 30 不给分布。 */
  median_pct: number | null;
  /** 最差 5% 分位（%，通常为负）；`null` = 同上。 */
  p05_pct: number | null;
}

/** `GET /api/v1/market/regime` 的响应（字段名与 spec §S10.3 的 JSON 逐字一致）。 */
export interface MarketRegime {
  /** 当前状态（五态之一）；`null`/空串 = 产物缺失/损坏/落后（页面整行不渲染）。 */
  state: string | null;
  /** 当前状态从哪天开始（ISO `YYYY-MM-DD`）；未知为 `null`。 */
  since: string | null;
  /** 已持续的自然/交易天数（后端算，前端不重算）；未知为 `null`。 */
  duration_days: number | null;
  /** 从**当前状态**出发的下一日状态分布（`{下一态: 概率}`，行和 ≈ 1）。 */
  transition: Record<string, number>;
  /** 同态后续收益分布，键是 horizon（`"5d"` / `"20d"`）。 */
  forward: Record<string, RegimeForwardBucket | null>;
  /** 口径与降级说明（后端产出的中性句）。 */
  notes: string[];
  /** 产物截至日（ISO）；未知为 `null`。 */
  as_of: string | null;
  /** 阈值版本（`REGIME_VERSION`）；未知为 `null`。 */
  version: string | null;
}

/** 有限数字才算数：`null` / `undefined` / `NaN` / 非数字一律 `null`（绝不 `?? 0`）。 */
function numOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function strOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim().length > 0 ? value : null;
}

/**
 * 容错归一：**任何**形状的 payload（`undefined` / `{}` / 半截对象）都归成一个合法
 * `MarketRegime`，绝不抛。
 *
 * 为什么需要它：后端（S10）与前端并行开发，联调前/降级时可能只给一半字段；而这一行
 * 是页面顶部的一行字——让它因为"缺个 `notes`"整页崩掉是本末倒置。缺的一律 `null`/空
 * 容器，由组件按"缺就不写"渲染（不补 0、不编"未知"）。
 */
export function normalizeRegime(raw: unknown): MarketRegime {
  const source = (raw ?? {}) as Record<string, unknown>;

  const transition: Record<string, number> = {};
  const rawTransition = source.transition;
  if (rawTransition && typeof rawTransition === "object") {
    for (const [to, value] of Object.entries(rawTransition as Record<string, unknown>)) {
      const prob = numOrNull(value);
      if (prob !== null) transition[to] = prob;
    }
  }

  const forward: Record<string, RegimeForwardBucket | null> = {};
  const rawForward = source.forward;
  if (rawForward && typeof rawForward === "object") {
    for (const [horizon, bucket] of Object.entries(rawForward as Record<string, unknown>)) {
      if (!bucket || typeof bucket !== "object") {
        // 该档整体缺数（后端按 `n < 30` 之外的原因给 null）：保留 key、"这一档没有分布"。
        forward[horizon] = null;
        continue;
      }
      const cell = bucket as Record<string, unknown>;
      forward[horizon] = {
        n: numOrNull(cell.n),
        median_pct: numOrNull(cell.median_pct),
        p05_pct: numOrNull(cell.p05_pct),
      };
    }
  }

  const notes = Array.isArray(source.notes)
    ? source.notes.filter((note): note is string => typeof note === "string" && note.trim().length > 0)
    : [];

  return {
    state: strOrNull(source.state),
    since: strOrNull(source.since),
    duration_days: numOrNull(source.duration_days),
    transition,
    forward,
    notes,
    as_of: strOrNull(source.as_of),
    version: strOrNull(source.version),
  };
}

/**
 * 读当前市场状态（登录即可）。
 *
 * **`skipAuth: true` 是刻意的**：`RequestOptions.skipAuth` 唯一的语义是"401 不触发全局
 * `auth:unauthorized` 清态"（见 `shared/api/client.ts`；`getSession` 用同一开关自行分类 401）。
 * 这一行是页面顶部的**装饰性读面**：它自己的失败已经按契约降级为"整行不渲染"，绝不该
 * 反过来把用户踢下线——会话真的失效时，`/watchlists/triage` 等真读面会照常触发清态。
 * 附带的好处是：既有 e2e 只 mock 自己消费的读面，一条没被 mock 的装饰请求也不会把
 * 整页变成登录页。
 */
export function fetchRegime(): Promise<MarketRegime> {
  return apiGet<unknown>("/api/v1/market/regime", undefined, { skipAuth: true }).then(
    normalizeRegime
  );
}
