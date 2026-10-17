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
