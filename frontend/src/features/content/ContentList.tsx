import { useMemo } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Tag, message } from "antd";
import { StateWrapper } from "@/shared/ui";
import { useAuth } from "@/features/auth/context";
import {
  fetchContent,
  updateContentState,
  type ContentFilters,
  type ContentItem,
  type ContentMetric,
  type ContentStateStatus,
} from "@/shared/api/content";
import "./ContentList.css";

/**
 * 内容列表（`/news` 公告 Tab 等读面）。
 *
 * 三条口径（对应后端 content 契约，别在 UI 层"美化"）：
 * - 原文链接是唯一入口（后端只存 URL，不落正文），点标题新窗口打开；
 * - 指标缺失显示 `—`，不显示 `0`：`0` 会被读成"营收为零"，而真相是"没抽到"；
 *   `extracted.missing_reason` 是诊断信息，允许以机器语言上屏（给了 muted 样式）；
 * - 状态按钮只在**登录后**出现：匿名请求的 `user_state` 恒为 `null`，
 *   给匿名用户一个点了会 401 的按钮，是把服务端约束画成前端 bug。
 */

const DEFAULT_LIMIT = 30;

/** 子类徽章文案；未知子类原样上屏（不吞掉后端新增类别）。 */
const SUBTYPE_LABEL: Record<string, string> = {
  report: "财报",
  event: "重大事项",
  equity_change: "股本变动",
};

/** 只展示这两类期间指标；列表里其余 metric_key 不上屏（契约要求）。 */
const PERIOD_METRICS: { key: string; label: string }[] = [
  { key: "revenue", label: "营业收入" },
  { key: "net_profit", label: "净利润" },
];

interface Props {
  contentType: string;
  emptyText?: string;
}

function subtypeOf(item: ContentItem): string | null {
  const raw = item.extracted?.subtype;
  return typeof raw === "string" && raw !== "" ? raw : null;
}

function missingReasonOf(item: ContentItem): string | null {
  const raw = item.extracted?.missing_reason;
  return typeof raw === "string" && raw !== "" ? raw : null;
}

/** `published_at` → `YYYY-MM-DD HH:mm`（本地时区，列表按人读的时间看）。 */
function formatPublishedAt(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(
    d.getMinutes()
  )}`;
}

/**
 * 元 → 亿元。`null` 保持 `null`（渲染成 `—`），**不**回落成 `0`。
 * 展示单位因此固定为「亿元」，与源 `unit`（可能是"元"）无关。
 */
function toYi(value: number | null | undefined): string | null {
  if (value === null || value === undefined || Number.isNaN(value)) return null;
  return (value / 1e8).toFixed(2);
}

function metricsOf(item: ContentItem): { label: string; metric: ContentMetric }[] {
  const out: { label: string; metric: ContentMetric }[] = [];
  for (const { key, label } of PERIOD_METRICS) {
    const metric = item.metrics.find((m) => m.metric_key === key);
    if (metric) out.push({ label, metric });
  }
  return out;
}

export function ContentList({ contentType, emptyText }: Props) {
  const queryClient = useQueryClient();
  const { isAuthenticated } = useAuth();

  // 引用稳定：filters 进 queryKey，每次渲染新建对象会让缓存键失效、列表反复重取。
  const filters = useMemo<ContentFilters>(
    () => ({ content_type: contentType, limit: DEFAULT_LIMIT }),
    [contentType]
  );

  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["content", contentType, filters],
    queryFn: () => fetchContent(filters),
  });

  const mutation = useMutation({
    mutationFn: ({ id, status }: { id: number; status: ContentStateStatus }) =>
      updateContentState(id, status),
    onSuccess: () => {
      // 只失效内容域：状态写回影响的是本用户看到的分组/徽标，不牵动其它域缓存。
      void queryClient.invalidateQueries({ queryKey: ["content"] });
    },
    onError: (err: Error) => {
      message.error(err.message || "状态更新失败");
    },
  });

  const items = data ?? [];
  const pendingId = mutation.isPending ? mutation.variables?.id : undefined;

  return (
    <StateWrapper
      loading={isLoading}
      error={error ?? null}
      empty={items.length === 0}
      emptyText={emptyText}
      onRetry={() => void refetch()}
    >
      <ul className="content-list">
        {items.map((item) => {
          const subtype = subtypeOf(item);
          const missingReason = missingReasonOf(item);
          const metrics = metricsOf(item);
          const publisher = item.publisher ?? item.source_name;

          return (
            <li className="content-list__row" data-testid="content-row" key={item.id}>
              <div className="content-list__main">
                <a
                  className="content-list__title"
                  data-testid="content-original-link"
                  href={item.url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {item.title}
                </a>

                <div className="content-list__meta">
                  <span className="content-list__publisher">{publisher}</span>
                  <time className="content-list__time" dateTime={item.published_at}>
                    {formatPublishedAt(item.published_at)}
                  </time>
                  {subtype ? (
                    <Tag className="content-list__tag" bordered={false}>
                      {SUBTYPE_LABEL[subtype] ?? subtype}
                    </Tag>
                  ) : null}
                </div>

                {missingReason ? (
                  <p className="content-list__missing">{missingReason}</p>
                ) : null}

                {metrics.length > 0 ? (
                  <dl className="content-list__metrics">
                    {metrics.map(({ label, metric }) => {
                      const yi = toYi(metric.value);
                      return (
                        <div className="content-list__metric" key={metric.metric_key}>
                          <dt className="content-list__metric-label">{label}</dt>
                          <dd className="content-list__metric-value">
                            <span className="content-list__metric-period">
                              {metric.period ?? "—"}
                            </span>
                            {yi === null ? (
                              <span className="content-list__metric-num">—</span>
                            ) : (
                              <span className="content-list__metric-num num">
                                {yi} 亿元
                              </span>
                            )}
                          </dd>
                        </div>
                      );
                    })}
                  </dl>
                ) : null}
              </div>

              {isAuthenticated ? (
                <div className="content-list__actions">
                  <Button
                    size="small"
                    loading={pendingId === item.id}
                    onClick={() => mutation.mutate({ id: item.id, status: "read" })}
                  >
                    已读
                  </Button>
                  <Button
                    size="small"
                    loading={pendingId === item.id}
                    onClick={() => mutation.mutate({ id: item.id, status: "ignored" })}
                  >
                    忽略
                  </Button>
                </div>
              ) : null}
            </li>
          );
        })}
      </ul>
    </StateWrapper>
  );
}
