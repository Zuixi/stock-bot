import { apiGet } from "./client";

/**
 * 数据新鲜度巡检（`GET /api/v1/market/data-freshness`）——`/market/data-sources` 的数据源。
 *
 * 后端契约：`app/schemas/market_data.py::DataFreshnessOut`（只读，`apply=False` 恒不触发回补）。
 * 字段一律 snake_case 直出：本文件**只做类型标注，不做字段改名**——这个端点的 payload 是
 * 运维读数（表名 / 列名 / 交易日窗口），改名会让 UI 与 `psql` 看到的两套名字，排障时对不上。
 *
 * 三类字段各司其职，UI **不得混用**：
 * - `domains`  —— **可自愈域**：对账器发现缺日会尝试回补（`refetched`）。`status` ∈ ok/refetched/stale。
 * - `coverage` —— **只读巡检域**：没有 `_refetch_*`，永不回补，只能在这里被看见。
 *   `cover_pct` **可为 null**（分母 = 0，即该域还没有任何交易日数据）——那是"分母为空"，
 *   **不是 0% 覆盖**；渲染必须走 `--`，绝不 `?? 0`（后端 `CoverageOut` 的注释就是这条纪律）。
 * - `failed_jobs` —— 最近失败的调度任务登记（`job_alert_service`），把"静默失败"变成可见。
 */

/** 单域覆盖率（`CoverageOut`） */
export interface CoverageDomain {
  /** 巡检域名 */
  domain: string;
  /** 实际查询的表（运维定位用） */
  table: string;
  /** 业务日期列 */
  date_column: string;
  /** 窗口内行数 */
  row_count: number;
  /** 有任何行的交易日数 */
  have_days: number;
  /** 完整日的交易日数（其余域 == have_days） */
  complete_days: number;
  /** 分母：该域区间内的交易日数 */
  trading_days: number;
  /** complete_days / trading_days；**null = 分母为 0（不是 0%）** */
  cover_pct: number | null;
  missing_days: string[];
  /** 窗口外/非交易日的合法行（当日盘中 poll 行等），不参与覆盖率计分 */
  extra_days: string[];
  first_day: string | null;
  last_day: string | null;
  /** ok | missing_table | missing_column（后者 = 该域压根没查成，计数恒 0） */
  status: string;
  /** 分母口径降级：主源缺日、由并集补齐（换分母必须可见） */
  degraded_trading_calendar: boolean;
}

/** 可自愈域的对账状态（`DomainFreshnessOut`） */
export interface RefreshableDomain {
  latest_in_db: string | null;
  missing_days: string[];
  partial_days: string[];
  /** 本次巡检**实际回补**的日期；`apply=False` 时恒为空 */
  refetched: string[];
  /** ok | refetched | stale */
  status: string;
}

/** 最近失败的调度任务（`JobFailureOut`） */
export interface FailedJob {
  job_id: string;
  error: string;
  /** ISO8601（Asia/Shanghai） */
  at: string;
}

export interface DataFreshness {
  as_of: string;
  expected_latest: string | null;
  expected_days: number;
  /** 股票名录规模（`stocks` 表） */
  universe: number;
  /** 最新期望日 `daily_quotes` 的实际行数（与 `universe` 并排看即可发现名录滞后） */
  quotes_symbols_latest: number | null;
  /** tushare 日历外呼降级成工作日启发式（分母口径变了） */
  degraded_calendar: boolean;
  /** 恒 false（本端点是只读巡检）——留在类型里是为了让"它永不为 true"可被断言 */
  apply: boolean;
  /** 可自愈域：域 key → 状态 */
  domains: Record<string, RefreshableDomain>;
  coverage: CoverageDomain[];
  failed_jobs: FailedJob[];
}

/** 只读巡检（绝不触发回补）。公开端点，无需登录。 */
export function fetchDataFreshness(): Promise<DataFreshness> {
  return apiGet<DataFreshness>("/api/v1/market/data-freshness");
}
