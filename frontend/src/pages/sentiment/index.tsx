import { useMemo } from "react";
import { Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchMetricLatest, type MetricLatest } from "@/shared/api/industryResearch";
import { fetchSentimentTrends } from "@/shared/api/limitUp";
import { formatCnDate } from "@/shared/ui";
import {
  CompositeHero,
  SENTIMENT_GROUPS,
  SENTIMENT_METRIC_KEYS,
  SentimentModuleIndex,
  SentimentModulePanel,
  fetchSentimentComposite,
  type SentimentComposite,
} from "@/features/sentiment";
import "@/features/sentiment/sentiment.css";

/**
 * `/sentiment` 情绪分析（S1.8–S1.13）——市场情绪的**深度读出台**。
 *
 * 定位：`/market` 的短线情绪 Tab 负责"一眼看懂"，本页负责"每个数字都能复核"——
 * 每个值都带口径（`metric-dictionary` 已裁定的原文）与数据 vintage（`as_of` / `freq` /
 * `caliber_version` / `missing_reason`）。故本页：
 *  - **没有 Tab**：左侧粘性模块索引 + 单列滚动（Tab 会把模块藏起来，与"读出台"相反）；
 *  - **没有图表**：绝大多数指标是"当前值 + 窗口/新鲜度"，一个数不需要图；
 *  - **只有一个模块表 + 一个渲染器**（`features/sentiment/moduleTable.ts` / `SentimentModulePanel`）；
 *  - **只有 hero 是卡片**，其余是发丝分隔的平面面板。
 *
 * 取数：三个受保护读面各一次（综合指数端点 / 通用指标读面 / 后端已算好的历史分位）。
 * 不轮询——本页是日频、可复核的读数，不是盘中看板。
 */

/** 页头的一句话状态（批准稿：一句话，不是 `·` 拼接的清单）。 */
function stateSentence(
  composite: SentimentComposite | undefined,
  isLoading: boolean,
  isError: boolean,
): string {
  if (isLoading) return "正在读取综合指数与各模块指标读数。";
  if (isError || !composite) {
    return "综合指数读数暂不可得，下方各模块的指标仍逐条标注口径与数据 vintage。";
  }
  const date = composite.asOf ? formatCnDate(composite.asOf) : "未知口径日";
  if (composite.insufficientHistory) {
    return `综合指数读到 ${date}，但历史样本不足，本页不给分位。`;
  }
  if (composite.percentile == null) {
    // 后端第二档"没有分位"：当期 value 为 NULL（样本够，但没有今日值可分位）
    return `综合指数读到 ${date}，但当期值算不出，本页不给分位。`;
  }
  // 旧口径行（extra 缺 participants / n_constituents）：没有"由 N/11 条腿合成"这句事实
  if ((composite.totalLegs ?? 0) <= 0 && composite.legs.length === 0) {
    return `综合指数读到 ${date}。`;
  }
  return `综合指数读到 ${date}，当日由 ${composite.legCount ?? "--"}/${composite.totalLegs ?? "--"} 条腿合成。`;
}

export default function SentimentPage() {
  const compositeQuery = useQuery({
    queryKey: ["sentiment", "composite"],
    queryFn: fetchSentimentComposite,
    staleTime: 5 * 60_000,
    // 端点由并行任务落地，可能暂时 404：失败要快、且如实显示"暂不可得"，不重试到超时
    retry: 1,
  });

  const metricsQuery = useQuery({
    // 显式列出模块表用到的 metric_key：表与请求同源，且未登记的 key 会响亮 404 而不是静默少一行
    queryKey: ["sentiment", "metrics", "market", SENTIMENT_METRIC_KEYS],
    queryFn: () => fetchMetricLatest(["market"], SENTIMENT_METRIC_KEYS),
    staleTime: 5 * 60_000,
  });

  const trendsQuery = useQuery({
    queryKey: ["sentiment", "trends", 250],
    queryFn: () => fetchSentimentTrends(250),
    staleTime: 5 * 60_000,
  });

  const metricsByKey = useMemo(
    () => new Map<string, MetricLatest>((metricsQuery.data ?? []).map((m) => [m.metricKey, m])),
    [metricsQuery.data],
  );

  return (
    <div className="sent-page">
      <SentimentModuleIndex />

      <div className="sent-column">
        <header className="sent-head">
          <Typography.Title level={4} className="sent-head__title">
            情绪分析
          </Typography.Title>
          <p className="sent-head__state" data-testid="sentiment-state">
            {stateSentence(compositeQuery.data, compositeQuery.isLoading, compositeQuery.isError)}
          </p>
        </header>

        {metricsQuery.isError ? (
          <p className="sent-module__note" data-testid="sentiment-metrics-unavailable">
            指标读面未取到（GET /api/v1/metrics/latest?scope_keys=market 失败或未登录）：下表数值一律留空，
            不显示 0。
          </p>
        ) : null}

        {SENTIMENT_GROUPS.map((group) => (
          <section className="sent-group" id={`sentiment-group-${group.key}`} key={group.key}>
            <h2 className="sent-group__label">{group.label}</h2>
            {group.key === "result" ? (
              <CompositeHero
                data={compositeQuery.data}
                isLoading={compositeQuery.isLoading}
                isError={compositeQuery.isError}
              />
            ) : null}
            {group.modules
              .filter((m) => !m.hero)
              .map((module) => (
                <SentimentModulePanel
                  key={module.key}
                  module={module}
                  metrics={metricsByKey}
                  metricsPending={metricsQuery.isPending}
                  trends={trendsQuery.data}
                />
              ))}
          </section>
        ))}
      </div>
    </div>
  );
}
