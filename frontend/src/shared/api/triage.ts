import { apiGet } from "./client";

/**
 * 自选体检（S8）读面：`GET /api/v1/watchlists/triage`。
 *
 * 口径（spec §S8.1/§S8.4/§S8.5）：
 * - 只陈述**已发生的事实**，`headline` / `facts` 由后端探测器产出，前端不做二次推断；
 * - `severity` 是**固定四枚举**（风险 / 事件 / 关注 / 背景），页面不得引入第五种分级；
 * - 载荷自带 `disclaimer` 与每条 `verify`（复核去处）——前端**不硬编码**这两处文案/链接语义，
 *   避免与后端合规常量各写一份、漂移后无人发现。
 * - `state` 是**显式**的页面状态：三种"看起来都是空"的情形（没自选 / 今天数据没就绪 /
 *   有自选且都平静）由契约区分，前端不再从 `items=[] && quiet_count=0` 猜
 *   （曾经把"今天查不了"渲染成"你还没加自选"）。
 */

/** 分级：只此四枚举（spec §S8.5「分级只用 风险/事件/关注/背景」）。 */
export type TriageSeverity = "risk" | "event" | "attention" | "background";

/**
 * 页面状态（与后端 `TriageState` 一一对应）：
 * - `ok`：有自选且已拿到最近完整交易日（能否展示事实看 `items` / `quiet_count`）；
 * - `no_data`：有自选，但最近完整交易日还不存在 → 「数据没就绪」，**不是**"没自选"；
 * - `no_watchlist`：用户一只自选都没有 → 给「添加自选」入口。
 */
export type TriageState = "ok" | "no_data" | "no_watchlist";

/** 一条事实：`label: value+unit`，例如 `收盘价 11.20元`。 */
export interface TriageFact {
  label: string;
  value: number | string;
  unit: string;
}

export interface TriageItem {
  symbol: string;
  name: string;
  /** 探测器 key（`break_ma20` 等），页面不直接上屏，仅作 React key 的补充。 */
  detector: string;
  severity: TriageSeverity;
  headline: string;
  facts: TriageFact[];
  /** 这条事实的数据截至（日级），页面走 `shared/ui/date.ts` 格式化后上屏。 */
  as_of: string;
  source: string;
  /** 复核去处：`kline` | `announcement` | `dragon_tiger` | `sector`（映射见 `TriagePanel`）。 */
  verify: string;
  /**
   * 规则出处（provenance，契约见 `.superpowers/sdd/triage-provenance/contract.md`）：
   * - `rule_text` 人话阈值（`5 日跌幅 ≥ 12%`），**后端产出**，前端不改写；
   * - `rule_version` 规则版本（`r1`），阈值改口径必须换版本，故要和 `rule_text` 一起上屏；
   * - `source_table` 主要数据表（`daily_quotes`），回答"这条事实是从哪张表算的"。
   *
   * 三者是**卡片行**的必需字段（与 `rules[]` 的 `rule_text` / `version` / `source_tables`
   * 同源）；`detector` 才是 `rules[].key`，前端只拿它做 `find`，永不上屏。
   */
  rule_text: string;
  rule_version: string;
  source_table: string;
}

/** 一条规则的历史统计（`rules[].stats`；`null` 时看 `stats_note`）。 */
export interface TriageRuleStats {
  as_of: string;
  window_months: number;
  signals: number;
  days: number;
  /** 每日约几条信号（可为小数，如 `5.1`）。 */
  per_day: number;
  /**
   * `tN_excess_med` = 各信号日"信号组中位收益 − 当日全市场中位收益"的跨日中位数（%）。
   * **可能为负**：展示必须保留符号（`-1.02%`），不要取绝对值、不要丢掉 `-`。
   * 后端若因样本不足给不出某一档，这里允许 `null`（页面显示 `—`，不显示 0）。
   */
  t1_excess_med: number | null;
  t5_excess_med: number | null;
  t20_excess_med: number | null;
  /** 信号组 5 日收益的 5% 分位（%，通常为负）。 */
  t5_p05: number | null;
  /** 统计口径一句话说明（页面原样展示，不二次解释）。 */
  basis: string;
}

/**
 * 一条规则的**说明面**（`GET /api/v1/watchlists/triage/rules` 的 `rules[]`）。
 *
 * 这份数据只服务「规则说明」抽屉：定义、阈值、数据来源、已知失效场景、历史统计。
 * 与 `TriageItem` 的分工是刻意的——卡片只说"发生了什么"，"这条规则怎么算、
 * 什么时候会算错、历史上是什么样"都在抽屉里，避免卡片变成长文。
 */
export interface TriageRule {
  /** 规则 key = `TriageItem.detector`（抽屉用它 `find` 当前规则）。 */
  key: string;
  /** 人话规则名（`跌得急`），抽屉标题用。 */
  label: string;
  severity: TriageSeverity;
  /** 规则版本（`r1`），与卡片上的 `rule_version` 同源。 */
  version: string;
  rule_text: string;
  definition: string;
  /** 阈值表：key 是口径字段名（`day_pct`），value 可能为负，原样展示。 */
  thresholds: Record<string, number>;
  source_tables: string[];
  /** 数据更新时间/口径说明（如「盘后 15:30 起可由 daily_quotes 计算」）。 */
  update_note: string;
  /** 已知失效场景（人话，中性陈述）。 */
  failure_modes: string[];
  /** `null` = 样本不足/数据不足，**页面不得显示 0 或空表**，改显示 `stats_note`。 */
  stats: TriageRuleStats | null;
  /** `stats` 为 `null` 时的原因说明（契约要求此时必填）。 */
  stats_note: string | null;
}

export interface TriageRulesResponse {
  rules: TriageRule[];
}

export interface TriageResponse {
  as_of: string;
  /** 页面状态：三态文案的唯一依据（见 `TriageState`）。 */
  state: TriageState;
  /** 今日无命中的自选只数（`state==='ok' && items=[]` 时用来区分「全静默」与「无法判定」）。 */
  quiet_count: number;
  /** 超出单页上限被折叠的条数（>0 时页面顶部给一行「另有 N 项未展示」）。 */
  folded: number;
  items: TriageItem[];
  /**
   * 缺数据而未执行的检查 —— **人话 label**（「所属板块在动」），可直接上屏。
   * spec §S8.4：**不得静默当作无事件**。
   */
  unavailable: string[];
  /**
   * 与 `unavailable` 对应的探测器 key（`sector_move` 之类）。
   * **禁止渲染**：这是机器串，上屏就是"把内部标识当文案"。仅排查/日志用。
   */
  unavailable_keys?: string[];
  disclaimer: string;
}

export function fetchTriage(): Promise<TriageResponse> {
  return apiGet<TriageResponse>("/api/v1/watchlists/triage");
}

/**
 * 规则说明（`GET /api/v1/watchlists/triage/rules`，登录即可）。
 *
 * 只在「规则说明」抽屉打开时取（`useQuery({ enabled: open })`）：卡片本身不需要它，
 * 每次进页面都拉一份规则说明是白给首屏增加一个请求。
 */
export function fetchTriageRules(): Promise<TriageRulesResponse> {
  return apiGet<TriageRulesResponse>("/api/v1/watchlists/triage/rules");
}

/**
 * 今日**命中数**（`/me` 的「今日体检」Tab 角标，spec §S12.2）：**展示条目数 + 折叠条数**。
 *
 * 为什么把 `folded` 算进来：`folded` 是"因单页上限没展示、但确实命中了"的条数
 * （spec §S8.4 的 `另有 N 项未展示`）。角标若只数 `items.length`，页面会同时出现
 * 「角标 8」与「另有 5 项未展示」——一个自称在数命中、却漏数命中自身的东西。
 *
 * `state` 不是 `ok`（`no_data` / `no_watchlist`）时 `items` 本来就是空、`folded` 为 0，
 * 故不需要额外分支：算出来就是 0（= 不显示角标），不会把"今天查不了"伪装成"有 0 条命中"。
 * 缺字段（后端半截 payload）按 0 处理即可——**这里**的 0 不是假话，它只是"没有可数的条目"。
 */
export function triageHitCount(data: TriageResponse | null | undefined): number | null {
  if (!data) return null;
  const items = Array.isArray(data.items) ? data.items.length : 0;
  const folded =
    typeof data.folded === "number" && Number.isFinite(data.folded)
      ? Math.max(0, Math.trunc(data.folded))
      : 0;
  return items + folded;
}

/**
 * 与 `fetchTriage` **同一读面**，但传 `skipAuth`：只给角标用。
 *
 * 角标是装饰性读面（`/me` 在「决策日志」tab 上时也要知道体检命中数），它 401 时不该
 * 触发全局清态把用户踢下线——同 `fetchRegime`/`fetchJournalSummary` 的口径。
 * `TriagePanel` 自己的请求仍走 `fetchTriage`（普通 `apiGet`），主线的 401 语义不动。
 */
export function fetchTriageBadge(): Promise<TriageResponse> {
  return apiGet<TriageResponse>("/api/v1/watchlists/triage", undefined, { skipAuth: true });
}
