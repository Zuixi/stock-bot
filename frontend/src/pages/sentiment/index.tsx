import { useEffect, useMemo, useRef } from "react";
import { Typography } from "antd";
import { useQuery, useQueryClient } from "@tanstack/react-query";
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
 * 不主动轮询行情——本页是日频、可复核的读数。但**追平之前**必须能自动更新：
 *  - 三个 query 都 `refetchOnWindowFocus: true`（覆盖 `App.tsx` 的全局 `false`）——用户切屏回来即拉最新；
 *  - 只在 composite `stale === true`（落后）时挂 5 分钟低频探测，后端追平后 `stale` 转 false ⇒ 自动停；
 *    **不**用 `provisional === true` 作轮询条件：临时值能持续一天以上，拿它轮询是空转；
 *  - composite 的 `asOf` 一变就让 metrics / trends 一起重取，否则会出现"hero 是今天、下方模块是昨天"。
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
  // 新鲜度先说（2026-10-16）：hero 只展示「各成分指标齐备」的交易日，所以"读到哪天"必须
  // 带上落后信息 —— 否则读者会把"各成分指标齐备的最后一天"直接当成"最新交易日"。
  // 三态：true 落后 / false 追平（不加字）/ null 判不出（**不得**沉默，沉默读成新鲜）。
  const freshness =
    composite.stale === true
      ? `，落后 ${composite.lagTradingDays ?? "--"} 个交易日（各成分指标齐备的数据已到 ${
          composite.expectedAsOf ? formatCnDate(composite.expectedAsOf) : "未知"
        }）`
      : composite.stale == null
        ? "（新鲜度无法判定）"
        : "";
  // 临时值三态（与新鲜度正交，同 hero）：true 会被重算 / false 终值（不加字）/ null 判不出。
  // **不承诺**"次日"——实测上游可能超过一天才披露。
  const provisional =
    composite.provisional === true
      ? "（本期为临时值：缺融资融券的 T+1 披露，上游披露后会重算，届时可能变化）"
      : composite.provisional == null
        ? "（无法判定本期是否为临时值）"
        : "";
  const head = `综合指数读到 ${date}${freshness}${provisional}`;
  if (composite.insufficientHistory) {
    return `${head}，但历史样本不足，本页不给分位。`;
  }
  if (composite.percentile == null) {
    // 后端第二档"没有分位"：当期 value 为 NULL（样本够，但没有今日值可分位）
    return `${head}，但当期值算不出，本页不给分位。`;
  }
  // 旧口径行（extra 缺 participants / n_constituents）：没有"由 N/11 个成分指标合成"这句事实
  if ((composite.totalLegs ?? 0) <= 0 && composite.legs.length === 0) {
    return `${head}。`;
  }
  return `${head}，当日由 ${composite.legCount ?? "--"}/${composite.totalLegs ?? "--"} 个成分指标合成。`;
}

export default function SentimentPage() {
  const queryClient = useQueryClient();

  const compositeQuery = useQuery({
    queryKey: ["sentiment", "composite"],
    queryFn: fetchSentimentComposite,
    staleTime: 5 * 60_000,
    // 端点由并行任务落地，可能暂时 404：失败要快、且如实显示"暂不可得"，不重试到超时
    retry: 1,
    // 后端交易日 17:30 后 `expected_as_of` 即当日：追平之前页面必须能自己更新。
    // 只在"落后"（stale === true）时 5 分钟探一次，追平（false）/ 判不出（null）都停。
    // 条件里**不**放 provisional：临时值可持续一天以上，拿它轮询是空转。
    refetchInterval: (q) => (q.state.data?.stale === true ? 300_000 : false),
    // 覆盖 App.tsx 的全局 false：用户切屏回来即拉最新（日频读数，零成本）
    refetchOnWindowFocus: true,
  });

  const metricsQuery = useQuery({
    // 显式列出模块表用到的 metric_key：表与请求同源，且未登记的 key 会响亮 404 而不是静默少一行
    queryKey: ["sentiment", "metrics", "market", SENTIMENT_METRIC_KEYS],
    queryFn: () => fetchMetricLatest(["market"], SENTIMENT_METRIC_KEYS),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: true,
  });

  const trendsQuery = useQuery({
    queryKey: ["sentiment", "trends", 250],
    queryFn: () => fetchSentimentTrends(250),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: true,
  });

  /**
   * hero 与下方模块必须讲同一天：composite 的 `asOf` 一变（例如 17:30 后 hero 前进到当日、
   * 或迟到数据追平），就让 metrics / trends 一起重取 —— 否则会出现"hero 是今天、下方模块是昨天"。
   *
   * `useRef` 记上一次的值，只在**真的变化**时触发（否则每次 render 都会 invalidate ⇒ 无限循环）。
   * 首次拿到值（prev === null，挂载时的并发请求已各取一次）与 `asOf` 变 null 都不触发。
   */
  const compositeAsOf = compositeQuery.data?.asOf ?? null;
  const lastAsOfRef = useRef<string | null>(null);
  useEffect(() => {
    const prev = lastAsOfRef.current;
    lastAsOfRef.current = compositeAsOf;
    if (prev === null || compositeAsOf === null || prev === compositeAsOf) return;
    void queryClient.invalidateQueries({ queryKey: ["sentiment", "metrics"] });
    void queryClient.invalidateQueries({ queryKey: ["sentiment", "trends"] });
  }, [compositeAsOf, queryClient]);

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
