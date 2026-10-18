import { useEffect, useId, useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { Button, Segmented, Skeleton, Tag } from "antd";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import * as echarts from "echarts";
import { EChart } from "@/shared/ui/EChart";
import { formatCnDate, qualityBadgeText, useIsNarrowViewport } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";
import {
  SENTIMENT_BENCHMARKS,
  fetchSentimentPulse,
  type SentimentBenchmarkCode,
  type SentimentMetricKey,
  type SentimentMetricUnit,
  type SentimentPulse,
  type SentimentPulsePoint,
} from "@/shared/api/limitUp";
import {
  formatMetricDisplay,
  metricAxisSuffix,
  metricAxisValue,
} from "@/features/sentiment/metricDetailFormat";
import { regimeBandData, withAlpha } from "@/features/sentiment/regimeStyle";
import "./SentimentPulse.css";

/**
 * 情绪脉搏（2026-10-19）：把「9 个指标 + 周期阶段 + 指数对比」放进一块**仪表带**。
 *
 * 设计取向（与详情页的卡片语言刻意不同）：
 *  - 不做九宫格卡片，用发丝规则切分——它是一条"纸带"，不是九个抽屉；
 *  - **阶段是主角**（左侧大字 + 图内低透明度背景带），不是又一个大数字；
 *  - 4 张 small multiples 共享同一根时间轴，hover 用 `echarts.connect` 联动；
 *  - 只用一种强调色（阶段色）+ 中性线，避免 9 条彩色线变成"意大利面"。
 *
 * 数据全部来自 `GET /market/sentiment/pulse`（读时计算，阶段 PIT、对比同分位轴）。
 */

const RANGES = [20, 60, 120, 250] as const;
type RangeDays = (typeof RANGES)[number];

const CORE_METRICS: Array<{
  key: SentimentMetricKey;
  label: string;
  unit: SentimentMetricUnit;
  value: (p: SentimentPulsePoint) => number | null;
  n?: (p: SentimentPulsePoint) => number | null;
}> = [
  { key: "zt_count", label: "涨停家数", unit: "count", value: (p) => p.ztCount },
  { key: "broken_rate", label: "炸板率", unit: "ratio", value: (p) => p.brokenRate },
  { key: "max_streak", label: "最高板", unit: "board", value: (p) => p.maxStreak },
  {
    key: "promo_1to2",
    label: "1进2 晋级率",
    unit: "ratio",
    value: (p) => p.promo1to2,
    n: (p) => p.promo1to2N,
  },
];

function shortDate(iso: string): string {
  return iso.slice(5);
}

function isRange(value: number): value is RangeDays {
  return (RANGES as readonly number[]).includes(value);
}

function isBenchmark(value: string | null): value is SentimentBenchmarkCode {
  return SENTIMENT_BENCHMARKS.some((b) => b.code === value);
}

/** 单张 small multiple：HTML 头 + 无装饰折线 + 阶段背景带。 */
function PulseCell({
  metric,
  data,
  group,
  showX,
  colors,
  bandOpacity,
}: {
  metric: (typeof CORE_METRICS)[number];
  data: SentimentPulse;
  group: string;
  showX: boolean;
  colors: ThemePalette;
  bandOpacity: number;
}) {
  const navigate = useNavigate();
  const dates = data.series.map((p) => p.tradeDate);
  const values = data.series.map((p) => {
    const v = metric.value(p);
    return v == null ? null : metricAxisValue(v, metric.unit);
  });
  const last = data.series[data.series.length - 1];
  const latest = last ? metric.value(last) : null;
  const latestN = last && metric.n ? metric.n(last) : null;
  const lowSample = latestN != null && latestN < 5;
  const pct =
    data.regime.current?.inputs.find((i) => i.key === metric.key)?.percentile ?? null;
  const href = `/market/sentiment/${metric.key}`;

  const option = useMemo(
    () => ({
      animation: false,
      grid: { left: 34, right: 10, top: 6, bottom: showX ? 22 : 6 },
      tooltip: {
        trigger: "axis",
        confine: true,
        formatter: (params: unknown) => {
          const list = params as Array<{ dataIndex: number }>;
          const point = data.series[list?.[0]?.dataIndex ?? -1];
          if (!point) return "";
          const n = metric.n ? metric.n(point) : null;
          const nText = n == null ? "" : `<br/>样本 n=${n}${n < 5 ? "（样本小）" : ""}`;
          return `<b>${point.tradeDate}</b><br/>${metric.label}：${formatMetricDisplay(
            metric.value(point),
            metric.unit,
          )}${nText}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        axisTick: { show: false },
        axisLabel: {
          show: showX,
          fontSize: 9,
          interval: Math.max(0, Math.floor(dates.length / 4) - 1),
          formatter: (v: string) => shortDate(v),
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        splitNumber: 2,
        axisLabel: { fontSize: 9, formatter: `{value}${metricAxisSuffix(metric.unit)}` },
        splitLine: { show: false },
      },
      series: [
        {
          type: "line",
          data: values,
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.4, color: colors.accent },
          itemStyle: { color: colors.accent },
          markArea: {
            silent: true,
            label: { show: false },
            data: regimeBandData(data.regime.segments, dates, colors, bandOpacity),
          },
        },
      ],
    }),
    [bandOpacity, colors, data, dates, metric, showX, values],
  );

  return (
    <div className="pulse-cell">
      <Link className="pulse-cell__head" to={href} title={`${metric.label}：查看历史追踪`}>
        <span className="pulse-cell__label">{metric.label}</span>
        <span className="pulse-cell__value num">{formatMetricDisplay(latest, metric.unit)}</span>
        {pct != null ? <span className="pulse-cell__pct num">{pct.toFixed(0)}%</span> : null}
        {latestN != null ? (
          <span className={`pulse-cell__n${lowSample ? " pulse-cell__n--warn" : ""}`}>
            n={latestN}
          </span>
        ) : null}
        <span className="pulse-cell__hint">↗</span>
      </Link>
      <EChart
        option={option}
        height={104}
        group={group}
        onEvents={{ click: () => navigate(href) }}
      />
    </div>
  );
}

/** 情绪 vs 指数：两条都转成 PIT 分位（0–100）再同轴比较；阴影 = 连续背离段。 */
function CompareChart({
  data,
  group,
  colors,
  narrow,
  bandOpacity,
}: {
  data: SentimentPulse;
  group: string;
  colors: ThemePalette;
  narrow: boolean;
  bandOpacity: number;
}) {
  const dates = data.comparison.map((p) => p.tradeDate);
  const option = useMemo(
    () => ({
      animation: false,
      // EChart 封装默认会给 legend 上 textStyle；这里显式关掉，避免与 HTML 图例重复
      legend: { show: false },
      grid: { left: 34, right: 10, top: 8, bottom: 22 },
      tooltip: {
        trigger: "axis",
        confine: true,
        formatter: (params: unknown) => {
          const list = params as Array<{ dataIndex: number }>;
          const point = data.comparison[list?.[0]?.dataIndex ?? -1];
          if (!point) return "";
          const ix = point.indexPct5d == null ? "--" : `${point.indexPct5d.toFixed(2)}%`;
          return (
            `<b>${point.tradeDate}</b><br/>` +
            `涨停家数分位：${point.sentimentPct == null ? "--" : point.sentimentPct.toFixed(1)}%<br/>` +
            `${data.benchmark.name} 5 日收益分位：${point.indexPct == null ? "--" : point.indexPct.toFixed(1)}%<br/>` +
            `${data.benchmark.name} 5 日收益：${ix}`
          );
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        axisTick: { show: false },
        axisLabel: {
          fontSize: 9,
          interval: Math.max(0, Math.floor(dates.length / 5) - 1),
          formatter: (v: string) => shortDate(v),
        },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 100,
        splitNumber: 2,
        axisLabel: { fontSize: 9, formatter: "{value}%" },
        splitLine: { show: false },
      },
      series: [
        {
          name: "涨停家数分位",
          type: "line",
          data: data.comparison.map((p) => p.sentimentPct),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.6, color: colors.accent },
          itemStyle: { color: colors.accent },
        },
        {
          name: "指数 5 日收益分位",
          type: "line",
          data: data.comparison.map((p) => p.indexPct),
          showSymbol: false,
          connectNulls: false,
          lineStyle: { width: 1.3, color: colors.textSecondary, type: "dashed" },
          itemStyle: { color: colors.textSecondary },
        },
      ],
    }),
    [colors, data.comparison, dates],
  );

  // 背离段用 markArea 叠在情绪线上（ECharts 的 markArea 属于 series，不是全局）
  const divergenceAreas = data.divergence.map((d) => [
    {
      xAxis: d.from,
      itemStyle: {
        color: withAlpha(
          d.kind === "sentiment_stronger" ? colors.up : colors.down,
          bandOpacity,
        ),
      },
    },
    { xAxis: d.to },
  ]);
  const merged = useMemo(
    () => ({
      ...option,
      series: option.series.map((s, i) =>
        i === 0
          ? {
              ...s,
              markArea: {
                silent: true,
                label: { show: false },
                data: divergenceAreas,
              },
            }
          : s,
      ),
    }),
    [bandOpacity, divergenceAreas, option],
  );

  return (
    <div className="pulse-compare">
      <div className="pulse-compare__head">
        <span className="pulse-compare__title">
          情绪 vs {data.benchmark.name}
          <span className="pulse-compare__axis">同分位轴</span>
        </span>
        <span className="pulse-compare__legend">
          <i className="pulse-compare__dot pulse-compare__dot--hot" />
          涨停家数分位
          <i className="pulse-compare__dot pulse-compare__dot--muted" />
          指数 5 日收益分位
        </span>
      </div>
      <EChart option={merged} height={narrow ? 112 : 124} group={group} />
      {data.currentDivergence ? (
        <p className="pulse-compare__note pulse-compare__note--warn">
          当前：{data.currentDivergence.label}，已连续 {data.currentDivergence.days} 个交易日
        </p>
      ) : (
        <p className="pulse-compare__note">
          当前：无连续背离（连续 3 日差 ≥30pp 才标记）
        </p>
      )}
    </div>
  );
}

export function SentimentPulse() {
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDays = Number(searchParams.get("pulse"));
  const days: RangeDays = isRange(rawDays) ? rawDays : 60;
  const rawBench = searchParams.get("bench");
  const benchmark: SentimentBenchmarkCode = isBenchmark(rawBench)
    ? rawBench
    : "399006.SZ";

  const query = useQuery({
    queryKey: ["sentiment", "pulse", days, benchmark],
    queryFn: () => fetchSentimentPulse(days, benchmark),
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: true,
    retry: 1,
    placeholderData: keepPreviousData,
  });

  const { colors, mode } = useTheme();
  const narrow = useIsNarrowViewport();
  const bandOpacity = mode === "dark" ? 0.035 : 0.07;
  const group = useId().replace(/:/g, "");

  // 卸载时断开联动组，避免 echarts 全局 group 表随路由切换无限增长
  useEffect(() => () => echarts.disconnect(group), [group]);

  const setParam = (key: string, value: string) => {
    const next = new URLSearchParams(searchParams);
    next.set(key, value);
    setSearchParams(next, { replace: true });
  };

  const data = query.data;

  return (
    <section className="pulse" data-testid="sentiment-pulse" aria-busy={query.isFetching}>
      <header className="pulse__head">
        <div className="pulse__heading">
          <h2 className="pulse__title">情绪脉搏</h2>
          <p className="pulse__sub">
            阶段、温度与指数，放在同一根时间轴上 <span className="pulse__mode">收盘口径</span>
          </p>
        </div>
        <div className="pulse__controls">
          <Segmented<number>
            size="small"
            value={days}
            onChange={(v) => setParam("pulse", String(v))}
            options={RANGES.map((r) => ({ label: `${r}D`, value: r }))}
          />
          <Segmented<SentimentBenchmarkCode>
            size="small"
            value={benchmark}
            onChange={(v) => setParam("bench", v)}
            options={SENTIMENT_BENCHMARKS.map((b) => ({ label: b.label, value: b.code }))}
          />
        </div>
      </header>

      {query.isFetching ? <div className="pulse__loading" aria-hidden="true" /> : null}

      {query.isError ? (
        <div className="pulse__empty">
          <span>情绪脉搏暂不可得（pulse 端点失败）。</span>
          <Button size="small" onClick={() => void query.refetch()}>
            重试
          </Button>
        </div>
      ) : !data ? (
        <div className="pulse__skeleton">
          <Skeleton active paragraph={{ rows: 5 }} />
        </div>
      ) : (
        <div className="pulse__body">
          <aside className="pulse__rail">
            <div className="pulse__stage-wrap">
              <span className={`pulse__stage pulse__stage--${data.regime.current?.tone ?? "muted"}`}>
                {data.regime.current?.label ?? "数据不足"}
              </span>
              <span className="pulse__stage-meta">
                {data.regime.current
                  ? `自 ${formatCnDate(data.regime.current.since)} 起 · ${data.regime.current.days} 天`
                  : "分位样本不足 20 天"}
              </span>
            </div>

            <dl className="pulse__inputs">
              {(data.regime.current?.inputs ?? []).map((input) => (
                <div className="pulse__input" key={input.key}>
                  <dt>{input.label}</dt>
                  <dd className="num">{formatMetricDisplay(input.value, input.unit)}</dd>
                  <span className="pulse__pct num">
                    {input.percentile == null ? "--" : `${input.percentile.toFixed(0)}% 分位`}
                  </span>
                </div>
              ))}
            </dl>

            <div className="pulse__diverge">
              {data.currentDivergence ? (
                <span className="pulse__diverge--warn">
                  {data.currentDivergence.label} · {data.currentDivergence.days} 日
                </span>
              ) : (
                <span>无连续背离</span>
              )}
            </div>

            <div className="pulse__legend" aria-hidden="true">
              <span>
                <i className="pulse__swatch pulse__swatch--cold" />
                冰点/退潮
              </span>
              <span>
                <i className="pulse__swatch pulse__swatch--neutral" />
                修复/分歧
              </span>
              <span>
                <i className="pulse__swatch pulse__swatch--hot" />
                发酵
              </span>
              <span>
                <i className="pulse__swatch pulse__swatch--extreme" />
                高潮
              </span>
            </div>

            <p className="pulse__asof">
              截至 {data.asOf ?? "--"}{" "}
              {qualityBadgeText(data.asOfQuality) ? (
                <Tag bordered={false} className="pulse__quality">
                  {qualityBadgeText(data.asOfQuality)}
                </Tag>
              ) : null}
              <br />
              <span className="pulse__disclaimer">阶段为启发式规则，不是交易信号</span>
            </p>
          </aside>

          <div className="pulse__charts">
            <div className={`pulse__grid${narrow ? " pulse__grid--narrow" : ""}`}>
              {CORE_METRICS.map((metric, index) => (
                <PulseCell
                  key={metric.key}
                  metric={metric}
                  data={data}
                  group={group}
                  // 2×2 时只有下排显示日期；窄屏单列时每张都显示
                  showX={narrow || index >= 2}
                  colors={colors}
                  bandOpacity={bandOpacity}
                />
              ))}
            </div>
            <CompareChart
              data={data}
              group={group}
              colors={colors}
              narrow={narrow}
              bandOpacity={mode === "dark" ? 0.035 : 0.06}
            />
          </div>
        </div>
      )}
    </section>
  );
}
