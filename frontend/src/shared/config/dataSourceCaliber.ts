/**
 * **口径常量**（Phase 4 §5.8）——一处定义，多处消费，杜绝"两个页面各写一句话"。
 *
 * ## 为什么需要这个文件（不是过度抽象）
 *
 * §5.8 的目标句是「**一处常量、两处表面、不漂移**」。改前的现实正是漂移的标本：
 * 「北向资金」的可用性在**两个页面**各写了一遍——`NorthboundCard` 写
 * `数据源已停更（最新 09-07）`，`/market/data-sources` 上那份静态矩阵写 `规划中`。
 * 两个说法都是"人写的一行字"，于是上游一有变化就各自漂移（`1efe65a` 删掉那份静态矩阵
 * 的直接原因就是它已经漂到与库内实况不一致）。
 *
 * 本文件把这句话变成**数据**：`NORTHBOUND_SOURCE` 是唯一原产地，渲染层只做拼接。
 *
 * ## 与后端的关系（口径不是"这里发明的"）
 *
 * - 北向的**数值口径**（`net_buy` / `turnover` / 跨断点 `mixed`）由后端
 *   `app/services/northbound_caliber.py`（Ruling 62/63）判定，随 payload 的
 *   `amount_kind` 下发——**本文件不复制那套判定**，只定义"这句话怎么写"。
 * - 北向的**可用性状态**由 payload 的 `source_status`（`live` | `discontinued`）下发
 *   （`GET /api/v1/market/northbound`，见 `app/api/v1/market_data.py:82`）——同样是读取，
 *   不是前端猜测。
 * - 文档口径：`docs/design/market-data-trust-and-realtime.md` 的 D3 行
 *   （"北向卡保留但标注数据源已停更"）+ `docs/design/data-source.md` 附.9 段
 *   （北向每日净买入自 2024-08-19 终止；现行是**成交额**口径）。
 */

/** 数据源状态（与后端 `source_status` 字段同值域，**不新增枚举**） */
export type SourceStatus = "live" | "discontinued";

/** 一个数据源的两面：**怎么称呼它** + **上行判定** + **状态文案** */
export interface DataSourceCaliber {
  /** 稳定 key（`data-testid` 的组成部分，不用中文做主键） */
  key: string;
  /** 展示名 */
  label: string;
  /** 一句话说明这个源提供什么 */
  scope: string;
  /** 上游产地（**内部文档用**；公开页不得渲染，见下文 `PUBLIC_SOURCE_DISCLOSURE`） */
  upstream: string;
  /** 状态 → 文案；每个源必须给出**全部**状态，缺一个就编译不过 */
  statusText: Record<SourceStatus, string>;
  /** 状态 → 徽章色（AntD Tag 的 `color`） */
  statusColor: Record<SourceStatus, string>;
}

/**
 * ⚠️ **公开页不得披露供应商名**：`e2e/marketTrust.spec.ts` 断言 `/market` 整页不出现
 * `TuShare` / `东方财富` / `东财` / `巨潮` / `AKShare`。故 `upstream` 字段**只许**在
 * 不需要遵守该门禁的页面（本仓库目前只有 `/market/data-sources` 用的是同一门禁口径，
 * 故它也不渲染 `upstream`）与**文档**里出现；任何组件都不得把 `upstream` 直接上屏。
 */
export const PUBLIC_SOURCE_DISCLOSURE = false;

/**
 * **北向资金**（沪深股通）——§5.8 的共享常量。
 *
 * 上游 `moneyflow_hsgt.north_money` 的两个事实写在 `statusText` 里：
 * - `discontinued`（上游停更）：`已接入 · 上游停更`
 * - `live`（仍在日更）：`已接入 · 上游日更`（**不写"正常"**：它只是"今天有数据"，
 *   不代表断点前的净买入口径回来了——那件事由 `amount_kind` 负责说）
 */
export const NORTHBOUND_SOURCE: DataSourceCaliber = {
  key: "northbound",
  label: "北向资金",
  scope: "沪深股通日频序列（2024-08-19 起为成交额口径；此前为净买入，两者不可拼接）",
  upstream: "tushare.moneyflow_hsgt",
  statusText: {
    discontinued: "已接入 · 上游停更",
    live: "已接入 · 上游日更",
  },
  statusColor: {
    discontinued: "orange",
    live: "green",
  },
};

/** 按状态取徽章文案（**唯一入口**：两个表面都必须走它，不许各自写字符串） */
export function sourceStatusText(src: DataSourceCaliber, status: SourceStatus): string {
  return src.statusText[status];
}

/** 按状态取徽章色 */
export function sourceStatusColor(src: DataSourceCaliber, status: SourceStatus): string {
  return src.statusColor[status];
}

/** 后端可能下发缺字段（`source_status` 可选）→ 缺省按**最保守**的 `discontinued` 处理：
 *  "不确定"必须读成"不可当实时用"，不得读成 `live`（那会把停更的数据当最新的用）。 */
export function normalizeSourceStatus(raw: string | null | undefined): SourceStatus {
  return raw === "live" ? "live" : "discontinued";
}

/**
 * 取北向信封的**同一个 query**（React Query 的 key + 窗口天数）。
 *
 * 为什么连 query 也要收进常量：两个表面若各自写 `useQuery(["market","northbound",30], …)`
 * 与 `…[...,5]…`，就会**各发一次请求**、拿到两份可能不一致的信封（一个刚刷过、
 * 一个还是缓存）——那正是"同一事实两个说法"的另一种形态。共用同一 key + 同一 `days`，
 * React Query 才会去重成一次请求、共享一份缓存。
 */
export const NORTHBOUND_QUERY = {
  /** React Query key（两个表面必须逐字相同） */
  key: ["market", "northbound", 30] as const,
  /** 窗口天数（= key 的第三个元素，改这里两个表面一起改） */
  days: 30,
};
