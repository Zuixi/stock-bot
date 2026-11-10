import { apiGet, apiPatch } from "./client";

/**
 * 内容读面客户端（S3.x）：`/api/v1/content` 列表 + 用户状态写回。
 *
 * 契约纪律（与后端 `app/schemas/content.py` 逐条对齐，前端不自行"补全"）：
 * - 列表返回**裸数组**，不套 `{items: …}` 信封（后端契约明确说明）；
 * - 列表里的 `metrics` **恒为空**，指标只在详情填充 —— 故列表行不会凭空出现
 *   `revenue` / `net_profit`；有值时按抽取结果渲染，没有时给 `—` 而不是 `0`
 *   （`0` 会被读成"营收为零"，而真相往往是"没抽取到"）；
 * - `user_state` 是**当前登录用户**的状态，匿名请求 `null`。前端不能把 `null`
 *   读成"未读"（那只对本用户成立，且匿名本来就没有状态可显示）；
 * - 可写入的状态只有四值（`new` 是入库默认值，不允许作为入参）。
 */

/** 内容 ↔ 实体关联：`entity_type` 决定跳转路由（stock / industry / policy_tag / concept）。 */
export interface ContentEntity {
  entity_type: string;
  entity_id: string;
}

/**
 * 从内容抽取的规范化指标。
 *
 * `value` 为 `null` 是**一等状态**（原文有指标但没抽到数字 / 口径不可比），
 * 渲染端必须显示 `—`，禁止 `?? 0`。
 */
export interface ContentMetric {
  metric_key: string;
  period: string | null;
  value: number | null;
  value_text: string | null;
  unit: string | null;
  source_span: Record<string, unknown>;
}

/** 一条内容的读面形状（`ContentItemOut`）。 */
export interface ContentItem {
  id: number;
  content_type: string;
  source_name: string;
  publisher_tier: string;
  publisher: string | null;
  title: string;
  /** ISO 时间戳；列表排序键，渲染时格式化为 `YYYY-MM-DD HH:mm`。 */
  published_at: string;
  /** 入库时点；与 `published_at` 的差值即新鲜度。 */
  ingested_at: string;
  /** 原文唯一入口（后端不落正文/PDF）。 */
  url: string;
  /** 抽取产物；`subtype` / `missing_reason` 等诊断字段都在这里。 */
  extracted: Record<string, unknown>;
  review_status: string;
  /** 当前用户的状态；匿名或未标记 → `null`。 */
  user_state: string | null;
  entities: ContentEntity[];
  /** 列表响应恒为空，详情才填充（后端契约）。 */
  metrics: ContentMetric[];
}

/** 列表筛选条件：与后端 query 参数同名（snake_case），不在这里做命名映射。 */
export interface ContentFilters {
  content_type?: string;
  subtype?: string;
  entity_type?: string;
  entity_id?: string;
  /** 按当前用户状态过滤；匿名请求带它会 401（后端契约）。 */
  status?: string;
  limit?: number;
  offset?: number;
}

/** 用户可写入的内容状态（与库 CHECK 约束一致，`new` 除外）。 */
export type ContentStateStatus = "read" | "processed" | "ignored" | "archived";

/** `GET /api/v1/content` → 裸数组，`published_at desc`。 */
export function fetchContent(filters: ContentFilters = {}): Promise<ContentItem[]> {
  return apiGet<ContentItem[]>("/api/v1/content", {
    content_type: filters.content_type,
    subtype: filters.subtype,
    entity_type: filters.entity_type,
    entity_id: filters.entity_id,
    status: filters.status,
    limit: filters.limit,
    offset: filters.offset,
  });
}

/**
 * `PATCH /api/v1/content/{id}/state` 写回当前用户的状态。
 *
 * 只写**当前登录用户**的那一行：同一内容对其他用户仍是各自的状态
 * （后端 `user_content_state` 按 `(user_id, content_id)` 唯一）。
 */
export async function updateContentState(id: number, status: ContentStateStatus): Promise<void> {
  await apiPatch<unknown>(`/api/v1/content/${id}/state`, { status });
}
