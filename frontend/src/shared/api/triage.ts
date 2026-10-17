import { apiGet } from "./client";

/**
 * 自选体检（S8）读面：`GET /api/v1/watchlists/triage`。
 *
 * 口径（spec §S8.1/§S8.4/§S8.5）：
 * - 只陈述**已发生的事实**，`headline` / `facts` 由后端探测器产出，前端不做二次推断；
 * - `severity` 是**固定四枚举**（风险 / 事件 / 关注 / 背景），页面不得引入第五种分级；
 * - 载荷自带 `disclaimer` 与每条 `verify`（复核去处）——前端**不硬编码**这两处文案/链接语义，
 *   避免与后端合规常量各写一份、漂移后无人发现。
 */

/** 分级：只此四枚举（spec §S8.5「分级只用 风险/事件/关注/背景」）。 */
export type TriageSeverity = "risk" | "event" | "attention" | "background";

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
}

export interface TriageResponse {
  as_of: string;
  /** 今日无命中的自选只数（`items=[]` 时前端据此区分「全静默」与「无自选」）。 */
  quiet_count: number;
  /** 超出单页上限被折叠的条数（>0 时页面顶部给一行「另有 N 项未展示」）。 */
  folded: number;
  items: TriageItem[];
  /** 缺数据而未执行的探测器（spec §S8.4：**不得静默当作无事件**）。 */
  unavailable: string[];
  disclaimer: string;
}

export function fetchTriage(): Promise<TriageResponse> {
  return apiGet<TriageResponse>("/api/v1/watchlists/triage");
}
