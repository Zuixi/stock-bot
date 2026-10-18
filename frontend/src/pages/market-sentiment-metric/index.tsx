import { useMemo } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { Breadcrumb, Button, Collapse, Empty, Result, Segmented, Skeleton, Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { EChart } from "@/shared/ui/EChart";
import { qualityBadgeText, useIsNarrowViewport } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import {
  fetchSentimentMetric,
  isSentimentMetricKey,
  type SentimentMetricDetail,
  type SentimentMetricKey,
  type SentimentMetricPoint,
  type SentimentMetricUnit,
} from "@/shared/api/limitUp";
import {
  formatMetricCompact,
  formatMetricDelta,
  formatMetricDisplay,
  metricAxisSuffix,
  metricAxisValue,
  metricUnitSuffix,
} from "@/features/sentiment/metricDetailFormat";
import { regimeBandData } from "@/features/sentiment/regimeStyle";
import "@/pages/market-sentiment-metric/metric-detail.css";

/** 可选窗口（交易日）。统计量/分位/分布随窗口重算；阶段分位恒用后端 250 日 PIT 窗口。 */
const RANGES = [20, 60, 120, 250] as const;
type RangeDays = (typeof RANGES)[number];
const DEFAULT_DAYS: RangeDays = 250;
/** 最近逐日表只列 20 行：详情页的主叙事是图，表是"对得上号"的抽样核对。 */
const TABLE_ROWS = 20;

const GROUP_LABEL: Record<string, string> = {
  breadth: "市场宽度",
  money: "赚钱效应",
  promotion: "接力强度",
  space: "空间高度",
};

function signedClass(value: number | null, hotterWhen: "high" | "low"): string {
  if (value == null || value === 0) return "smd-delta--flat";
  // 颜色表达「对情绪是热还是冷」，不是「数字正负」：炸板率上升是变冷（绿），跌停家数上升也是。
  const hotter = (value > 0) === (hotterWhen === "high");
  return hotter ? "smd-delta--hot" : "smd-delta--cold";
}

function valueTone(value: number | null, unit: SentimentMetricUnit): string {
  if (value == null) return "smd-value--muted";
  if (unit === "pct") return value > 0 ? "smd-value--hot" : value < 0 ? "smd-value--cold" : "";
  return "";
}

/** 单个箱的 x 轴标签：区间两端的展示值（含单位）。 */
function binLabel(from: number, to: number, unit: SentimentMetricUnit): string {
  return `${formatMetricCompact(from, unit)}~${formatMetricCompact(to, unit)}`;
}

export default function SentimentMetricPage() {
  const { metric } = useParams<{ metric: string }>();
  const [searchParams, setSearchParams] = useSearchParams();
  const rawDays = Number(searchParams.get("days"));
  const days: RangeDays = (RANGES as readonly number[]).includes(rawDays)
    ? (rawDays as RangeDays)
    : DEFAULT_DAYS;
  const valid = isSentimentMetricKey(metric);
  const metricKey: SentimentMetricKey = valid ? metric : "zt_count";

  const query = useQuery({
    queryKey: ["sentiment", "metric", metricKey, days],
    queryFn: () => fetchSentimentMetric(metricKey, days),
    // 日频读数：切屏回来拉一次即可；不轮询，也不在 404 上重试到超时
    staleTime: 5 * 60_000,
    refetchOnWindowFocus: true,
    retry: 1,
    enabled: valid,
    // 切换指标时保留上一份数据：否则新 query key 会先变成 isPending，整块正文被骨架替换，
    // 页面高度骤降再长回来 —— 这正是用户看到的"点 tab 整页抖动"。首载仍走骨架。
    placeholderData: keepPreviousData,
  });

  if (!valid) {
    return (
      <div className="smd">
        <Result
          status="404"
          title="未知的情绪指标"
          subTitle={`/market/sentiment/${metric ?? ""} 不在 9 个情绪指标里。`}
          extra={
            <Link to="/market?tab=sentiment">
              <Button type="primary">返回短线情绪</Button>
            </Link>
          }
        />
      </div>
    );
  }

  if (query.isError) {
    return (
      <div className="smd">
        <Result
          status="error"
          title="指标历史读取失败"
          subTitle="后端 /api/v1/market/sentiment/metrics 未取到。缺失不补 0，本页不显示任何估算值。"
          extra={
            <div className="smd-actions">
              <Button type="primary" onClick={() => void query.refetch()}>
                重试
              </Button>
              <Link to="/market?tab=sentiment">
                <Button>返回短线情绪</Button>
              </Link>
            </div>
          }
        />
      </div>
    );
  }

  const data = query.data;
  // 切换中 `data` 仍是上一个指标的完整响应：用它决定标题/选中态，保证"看到的数字与标签一致"，
  // 新数据到达后整体切换一次（而不是标签先变、数字后到）。
  const activeKey = data?.metric.key ?? metricKey;

  return (
    <div className="smd">
      <Breadcrumb
        className="smd-crumb"
        items={[
          { title: <Link to="/market">行情中心</Link> },
          { title: <Link to="/market?tab=sentiment">短线情绪</Link> },
          { title: data?.metric.label ?? "指标详情" },
        ]}
      />

      <header className="smd-head">
        {/* 返回是"离开本页"的动作，做成带边框的按钮而不是一行裸文字：
            有 hover/focus 态、有明确命中区，移动端也点得到。 */}
        <Link className="smd-back" to="/market?tab=sentiment" aria-label="返回短线情绪">
          <ArrowLeftOutlined />
          <span>短线情绪</span>
        </Link>

        <div className="smd-head__main">
          <Typography.Title level={4} className="smd-head__title">
            {data?.metric.label ?? "情绪指标"} <span className="smd-head__tail">历史追踪</span>
          </Typography.Title>
          <div className="smd-meta">
            {data ? (
              <>
                <span className="smd-meta__chip">情绪温度计</span>
                <span className="smd-meta__chip">
                  {GROUP_LABEL[data.metric.group] ?? data.metric.group}
                </span>
                <span className="smd-meta__sep" aria-hidden="true" />
                <span className="smd-meta__item">
                  数据截至 <b className="num">{data.asOf ?? "--"}</b>
                </span>
                {qualityBadgeText(data.asOfQuality) ? (
                  <Tag className="smd-meta__quality" bordered={false}>
                    {qualityBadgeText(data.asOfQuality)}
                  </Tag>
                ) : null}
                <span className="smd-meta__item">
                  有效样本 <b className="num">{data.stats?.sampleCount ?? 0}</b> / 窗口{" "}
                  <b className="num">{data.window.samples}</b>
                </span>
              </>
            ) : (
              "正在读取历史序列。"
            )}
          </div>
        </div>
      </header>

      <div className="smd-range">
        <span className="smd-range__label">窗口</span>
        <Segmented<RangeDays>
          size="small"
          value={days}
          onChange={(value) => {
            const next = new URLSearchParams(searchParams);
            next.set("days", String(value));
            setSearchParams(next, { replace: true });
          }}
          options={RANGES.map((r) => ({ label: `${r}D`, value: r }))}
        />
        <span className="smd-range__hint">统计/分位随窗口重算；阶段背景带恒用 250 日 PIT</span>
      </div>

      {/* 切换指标时的进度条：数据保留、只在顶部给一条 2px 指示（不闪骨架、不占位）。
          首载（还没有任何 data）不显示它，由下方骨架负责。 */}
      {data && query.isFetching ? (
        <div className="smd-loading" role="progressbar" aria-label="正在切换指标" />
      ) : null}

      {query.isPending || !data ? (
        <div className="smd-card">
          <Skeleton active paragraph={{ rows: 6 }} />
        </div>
      ) : (
        <>
          <Hero data={data} />
          <PeerSwitcher data={data} current={activeKey} />
          <TrendCard data={data} />
          <div className="smd-grid">
            <DistributionCard data={data} />
            <StatsCard data={data} />
          </div>
          <RecentTrend data={data} />
          <CaliberCard data={data} />
        </>
      )}
    </div>
  );
}

/** 今日读数 + 历史位置：回答"现在什么水平、在历史上有多极端"。 */
function Hero({ data }: { data: SentimentMetricDetail }) {
  const { unit, hotterWhen, label } = data.metric;
  const latest = data.latest?.value ?? null;
  const lowSample = data.latest?.lowSample ?? false;
  const rank = data.percentileRank;
  const streak = data.streak;
  return (
    <section className="smd-hero" data-testid="sentiment-metric-hero">
      <div className="smd-hero__value">
        <span className={`smd-value num ${valueTone(latest, unit)}`}>
          {formatMetricDisplay(latest, unit)}
        </span>
        {unit === "count" || unit === "board" ? (
          <span className="smd-value__unit">{metricUnitSuffix(unit)}</span>
        ) : null}
        {!lowSample ? (
          <span className={`smd-delta num ${signedClass(data.delta, hotterWhen)}`}>
            {formatMetricDelta(data.delta, unit)}
            <span className="smd-delta__cap">较前值</span>
          </span>
        ) : (
          <Tag className="smd-low-sample" bordered={false}>
            样本不足
          </Tag>
        )}
      </div>

      {/* 区间/类型说明**固定一行**（所有指标都渲染）：ratio 指标不会因为多一行把 hero 顶高。
          完整 n 放在 title 里，窄屏也不换行。 */}
      <p
        className="smd-hero__sub num"
        title={
          data.latestInterval
            ? `Wilson 95% 区间（n=${data.latestInterval.n}）`
            : "该指标没有二项比例区间"
        }
      >
        {data.latestInterval
          ? `95% 区间 [${formatMetricCompact(data.latestInterval.low, unit)}, ${formatMetricCompact(
              data.latestInterval.high,
              unit,
            )}] · n=${data.latestInterval.n}`
          : unit === "ratio"
            ? "样本不足，无区间"
            : unit === "pct"
              ? "均值型指标，无比例区间"
              : "计数型指标，无比例区间"}
      </p>

      <p className="smd-hero__rank">
        {lowSample ? (
          <span className="smd-warn">
            当日样本 n={data.latest?.n ?? "--"} &lt; {data.sampleGate?.minN ?? 5}
            ，原始值仍展示，但不参与统计量与分位 —— 小样本的百分比不是信号。
          </span>
        ) : data.insufficientHistory || rank == null ? (
          <span className="smd-warn">
            历史样本不足（有效样本 {data.stats?.sampleCount ?? 0} &lt; 60），本页不给分位 —— 不给假位置。
          </span>
        ) : (
          <>
            当前值位于近 <b className="num">{data.window.samples}</b> 个交易日的{" "}
            <b className="num">{rank.toFixed(1)}%</b> 分位（{label}在{" "}
            <b className="num">{data.window.from}</b> → <b className="num">{data.window.to}</b>{" "}
            的窗口里）。
          </>
        )}
      </p>

      <p className="smd-hero__hint">
        {hotterWhen === "high" ? "数值越高 = 情绪越热" : "数值越高 = 情绪越冷"}
        {streak.side != null && streak.days > 0 ? (
          <>
            {" · "}已连续 <b className="num">{streak.days}</b> 个交易日
            {streak.side === "above" ? "高于" : "低于"}窗口中位数
            <b className="num"> {formatMetricDisplay(streak.median, unit)}</b>
          </>
        ) : (
          " · 最新一日没有可判定的连续方向"
        )}
        {data.latest?.n != null ? (
          <>
            {" · "}当日样本{" "}
            <b className={`num${lowSample ? " smd-warn" : ""}`}>{data.latest.n}</b>
          </>
        ) : null}
      </p>
    </section>
  );
}

/** 9 个指标的横向切换：让用户"在同一张图上"熟悉相邻指标，而不是回退再点一次。 */
function PeerSwitcher({
  data,
  current,
}: {
  data: SentimentMetricDetail;
  current: SentimentMetricKey;
}) {
  return (
    <nav className="smd-switch" aria-label="切换情绪指标">
      {data.peers.map((peer) => {
        const active = peer.key === current;
        return (
          <Link
            key={peer.key}
            to={`/market/sentiment/${peer.key}`}
            replace
            className={`smd-switch__item${active ? " smd-switch__item--active" : ""}`}
            aria-current={active ? "page" : undefined}
          >
            <span className="smd-switch__label">{peer.label}</span>
            <span className="smd-switch__value num">
              {formatMetricDisplay(peer.latestValue, peer.unit)}
              {peer.lowSample ? <span className="smd-switch__warn">*</span> : null}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

/** 主图：逐日值 + P25/P50/P75 参考线 + dataZoom。 */
function TrendCard({ data }: { data: SentimentMetricDetail }) {
  const { colors, mode } = useTheme();
  const narrow = useIsNarrowViewport();
  // 窄屏（<768）画布只有 ~340px：三条分位线的标签会叠在数据上、互相压字。
  // 此时改为图下一行静态文案（与"窗口统计"同值），图里只保留虚线。
  const refText = [
    ["P25", data.stats?.p25],
    ["P50", data.stats?.median],
    ["P75", data.stats?.p75],
  ]
    .filter((r): r is [string, number] => r[1] != null)
    .map(([name, value]) => `${name} ${formatMetricDisplay(value, data.metric.unit)}`)
    .join(" · ");
  const option = useMemo(() => {
    const { unit } = data.metric;
    const dates = data.series.map((p) => p.tradeDate);
    // 样本不足的日子**断开主线**，另用空心散点显示原始值：否则 n=2 的 50% 会画成
    // 和 200 个样本一样的尖峰/深坑，读起来就是假信号。
    const values = data.series.map((p) =>
      p.value == null || p.lowSample ? null : metricAxisValue(p.value, unit),
    );
    const lowSamplePoints = data.series.map((p) =>
      p.lowSample && p.value != null ? metricAxisValue(p.value, unit) : null,
    );
    const refs = [
      { name: "P25", value: data.stats?.p25 },
      { name: "P50", value: data.stats?.median },
      { name: "P75", value: data.stats?.p75 },
    ]
      .filter((r): r is { name: string; value: number } => r.value != null)
      // 标签带值：只有 P25/P50/P75 三个字母时，读者还得回"窗口统计"里对数字。
      .map((r) => ({ ...r, label: `${r.name} ${formatMetricCompact(r.value, unit)}` }));

    return {
      animation: false,
      // 详情页标题已经写清指标名，图内不再放单条图例（避免重复占高）
      legend: { show: false },
      grid: { left: 56, right: 44, top: 24, bottom: 64 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const list = params as Array<{ dataIndex: number; value: number | null }>;
          const first = list?.[0];
          if (!first) return "";
          const point = data.series[first.dataIndex];
          if (!point) return "";
          const n = point.n == null ? "" : `<br/>样本 n=${point.n}`;
          return `<b>${point.tradeDate}</b><br/>${data.metric.label}：${
            point.value == null ? "--" : formatMetricDisplay(point.value, unit)
          }${n}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        boundaryGap: false,
        // 强制包含末点，但**不**用 showMaxLabel（它会把末点贴在倒数第二个标签上：
        // "26-10-13 26-10-16" 会叠字）。用 interval 回调固定 6 个锚点 + 末点。
        axisLabel: {
          formatter: (v: string) => v.slice(5),
          interval: (index: number) =>
            index === dates.length - 1 ||
            index % Math.max(1, Math.ceil(dates.length / 6)) === 0,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        axisLabel: { formatter: `{value}${metricAxisSuffix(unit)}` },
      },
      // 默认展示**整窗**（标题写的是"近 N 个交易日"，图就必须是那 N 天）；
      // 想看细节用滚轮/拖动下方滑块缩放，而不是首屏只给最后 120 天却标着 244。
      dataZoom: [
        { type: "inside", start: 0, end: 100 },
        { type: "slider", height: 18, bottom: 8 },
      ],
      series: [
        {
          type: "line",
          data: values,
          // 有效点之间跨过"样本不足"的日子连线（线是趋势，不是逐点断言）；
          // 低样本的原始值由空心散点单独标出，绝不画进主线。
          connectNulls: true,
          showSymbol: false,
          smooth: false,
          lineStyle: { width: 1.8, color: colors.accent },
          itemStyle: { color: colors.accent },
          // 阶段背景带（与 pulse 同一套 PIT 规则）：只在当前窗口内裁剪绘制。
          markArea: {
            silent: true,
            label: { show: false },
            data: regimeBandData(data.regime?.segments ?? [], dates, colors, mode === "dark" ? 0.03 : 0.05),
          },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: `${colors.accent}33` },
                { offset: 1, color: `${colors.accent}00` },
              ],
            },
          },
          markLine: {
            silent: true,
            symbol: "none",
            // 不在图内贴标签：P25/P50/P75 数值接近时三条标签会互相压字（炸板率实测）。
            // 图下一行 `smd-card__refs` 用同一组数值做静态说明，图里只留虚线。
            label: { show: false },
            lineStyle: { type: "dashed", width: 1, color: colors.border },
            data: refs.map((r) => ({
              name: r.label,
              yAxis: metricAxisValue(r.value, unit),
              lineStyle: { type: "dashed", color: colors.border },
            })),
          },
        },
        {
          type: "scatter",
          data: lowSamplePoints,
          symbolSize: 5,
          itemStyle: {
            color: "transparent",
            borderColor: colors.textSecondary,
            borderWidth: 1.3,
          },
          z: 4,
        },
      ],
    };
  }, [colors, data, mode, narrow]);

  return (
    <section className="smd-card" data-testid="sentiment-metric-trend">
      <header className="smd-card__head">
        <h2>近 {data.window.samples} 个交易日走势</h2>
        <span className="smd-card__note">
          背景带 = 周期阶段 · 虚线 = 窗口分位 · 空心 = 样本不足
        </span>
      </header>
      <EChart option={option} height={narrow ? 260 : 330} />
      <div className="smd-card__footrow">
        {refText ? <p className="smd-card__refs">分位参考：{refText}</p> : null}
        {data.regime?.current ? (
          <p className="smd-card__refs smd-regime-key">
            阶段：
            <i className="smd-key smd-key--cold" />
            冰点/退潮
            <i className="smd-key smd-key--neutral" />
            修复/分歧
            <i className="smd-key smd-key--hot" />
            发酵
            <i className="smd-key smd-key--extreme" />
            高潮
          </p>
        ) : null}
      </div>
    </section>
  );
}

/** 分布：当前值落在历史哪个区间、该区间有多少天。 */
function DistributionCard({ data }: { data: SentimentMetricDetail }) {
  const { colors } = useTheme();
  const { unit } = data.metric;
  const narrow = useIsNarrowViewport();
  const current = data.latest?.value;
  // 低样本日不进分布：不能拿一个"没参与统计"的值去分布在有效样本上标落点。
  const showCurrent = current != null && !data.latest?.lowSample;
  const currentBin =
    current == null
      ? null
      : (data.distribution.find(
          (bin, i) =>
            current >= bin.from && (current < bin.to || i === data.distribution.length - 1),
        ) ?? null);
  const option = useMemo(() => {
    const labels = data.distribution.map((bin) => binLabel(bin.from, bin.to, unit));
    const current = data.latest?.value;
    return {
      animation: false,
      // top 留 42：`当前` 标签要压在高出最高柱的位置，20 会和 y 轴名"天数"撞在一起。
      // 窄屏再让 y 轴名消失（标签落在第 1~2 箱时会正好压住它），天数改由卡片注说明。
      grid: { left: 48, right: 18, top: narrow ? 30 : 42, bottom: 56 },
      tooltip: { trigger: "axis" },
      xAxis: {
        type: "category",
        data: labels,
        axisLabel: { rotate: 40, fontSize: 10, interval: 0 },
      },
      yAxis: {
        type: "value",
        name: narrow ? undefined : "天数",
        // 名字放轴顶端、右对齐到轴线，给 `当前` 标签让出正上方。
        nameLocation: "end",
        nameGap: 10,
        nameTextStyle: { fontSize: 10, align: "left" },
        minInterval: 1,
      },
      series: [
        {
          type: "bar",
          data: data.distribution.map((bin) => bin.count),
          itemStyle: { color: colors.accent, opacity: 0.55 },
          barMaxWidth: 26,
          markLine:
            current == null || !showCurrent
              ? undefined
              : {
                  silent: true,
                  symbol: ["none", "none"],
                  // `position: "end"` = 竖线顶端；标签水平居中 + 底色，避免压住柱子或轴名。
                  label: {
                    formatter: `当前 ${formatMetricDisplay(current, unit)}`,
                    position: "end",
                    distance: 6,
                    align: "center",
                    verticalAlign: "bottom",
                    color: colors.up,
                    fontSize: 10,
                    fontWeight: 600,
                    backgroundColor: colors.bgPanel,
                    borderColor: colors.up,
                    borderWidth: 1,
                    borderRadius: 3,
                    padding: [2, 5],
                  },
                  lineStyle: { color: colors.up, type: "solid", width: 1.5 },
                  data: [
                    {
                      // 当前值所在箱（区间为半开 [from,to)，最后一箱含右端）
                      xAxis: Math.max(
                        0,
                        Math.min(
                          data.distribution.findIndex(
                            (bin, i) =>
                              current >= bin.from &&
                              (current < bin.to || i === data.distribution.length - 1),
                          ),
                          data.distribution.length - 1,
                        ),
                      ),
                    },
                  ],
                },
        },
      ],
    };
  }, [colors, data, narrow, unit]);

  return (
    <section className="smd-card" data-testid="sentiment-metric-distribution">
      <header className="smd-card__head">
        <h2>历史分布</h2>
        <span className="smd-card__note">
          等宽 10 箱 · 纵轴为天数
        </span>
      </header>
      {data.distribution.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={`有效样本不足 10 个（当前 ${data.stats?.sampleCount ?? 0}），不画分布`}
        />
      ) : (
        <>
          <EChart option={option} height={260} />
          <p className="smd-card__foot">
            {current == null || currentBin == null
              ? "当日无值，标不出落点。"
              : data.latest?.lowSample
                ? `当日样本 n=${data.latest?.n ?? "--"} < ${data.sampleGate?.minN ?? 5}，未进入分布；该区间在有效样本中 ${currentBin.count} 天（占 ${(
                    (currentBin.count / (data.stats?.sampleCount || 1)) *
                    100
                  ).toFixed(1)}%）。`
                : `当前值 ${formatMetricDisplay(current, unit)} 落在 ${binLabel(
                    currentBin.from,
                    currentBin.to,
                    unit,
                  )} 箱：该箱 ${currentBin.count} 天，占有效样本 ${(
                    (currentBin.count / (data.stats?.sampleCount || 1)) *
                    100
                  ).toFixed(1)}%。`}
          </p>
        </>
      )}
    </section>
  );
}

function StatsCard({ data }: { data: SentimentMetricDetail }) {
  const { unit } = data.metric;
  const stats = data.stats;
  const rows: Array<[string, number | null | undefined, string]> = [
    ["最小值", stats?.min, "窗口内最低"],
    ["P25", stats?.p25, "四分之一分位"],
    ["中位数", stats?.median, "窗口中枢"],
    ["P75", stats?.p75, "四分之三分位"],
    ["最大值", stats?.max, "窗口内最高"],
    ["均值", stats?.mean, "算术平均"],
    ["标准差", stats?.std, "总体标准差"],
  ];
  return (
    <section className="smd-card" data-testid="sentiment-metric-stats">
      <header className="smd-card__head">
        <h2>窗口统计</h2>
        <span className="smd-card__note">
          有效样本 {stats?.sampleCount ?? 0} / 窗口 {data.window.samples}
        </span>
      </header>
      {stats == null ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description="有效样本不足 5 个，不给均值/分位（避免把噪声读成事实）"
        />
      ) : (
        <>
          <dl className="smd-stats">
            {rows.map(([label, value, hint]) => (
              <div className="smd-stats__item" key={label}>
                <dt>{label}</dt>
                <dd className="num">{formatMetricDisplay(value, unit)}</dd>
                <small>{hint}</small>
              </div>
            ))}
          </dl>
          <p className="smd-card__foot">
            {data.sampleGate
              ? `样本门槛 n≥${data.sampleGate.minN}：${data.sampleGate.excludedDays} 天被排除（原始值仍在图上）`
              : "计数型指标：无样本门槛"}
          </p>
        </>
      )}
    </section>
  );
}

/** 最近 20 个交易日：图上的点能对回具体日期与样本量。 */
function RecentTrend({ data }: { data: SentimentMetricDetail }) {
  const { colors } = useTheme();
  const { unit } = data.metric;
  const narrow = useIsNarrowViewport();

  // 近 20 个交易日（升序）。`delta` 相对**前一交易日**：前一日缺失就不给变化，
  // 不跨 NULL 去比"上一有效日"——那会把隔了几天的变化读成"昨天→今天"。
  const points = useMemo(() => {
    const start = Math.max(0, data.series.length - TABLE_ROWS);
    return data.series.slice(start).map((point, i) => {
      const prev = data.series[start + i - 1];
      const delta =
        point.value != null && prev?.value != null ? point.value - prev.value : null;
      return { ...point, delta };
    });
  }, [data.series]);

  const option = useMemo(() => {
    const dates = points.map((p) => p.tradeDate.slice(5));
    const values = points.map((p) =>
      p.value == null || p.lowSample ? null : metricAxisValue(p.value, unit),
    );
    const lowSamplePoints = points.map((p) =>
      p.lowSample && p.value != null ? metricAxisValue(p.value, unit) : null,
    );
    const last = points[points.length - 1];
    return {
      animation: false,
      legend: { show: false },
      grid: { left: 44, right: 52, top: 24, bottom: 26 },
      tooltip: {
        trigger: "axis",
        formatter: (params: unknown) => {
          const list = params as Array<{ dataIndex: number }>;
          const point = points[list?.[0]?.dataIndex ?? -1];
          if (!point) return "";
          const delta = point.delta == null ? "--" : formatMetricDelta(point.delta, unit);
          const n =
            point.n == null
              ? ""
              : `<br/>样本 n=${point.n}${point.lowSample ? "（样本小，不参与统计）" : ""}`;
          return `<b>${point.tradeDate}</b><br/>${data.metric.label}：${formatMetricDisplay(
            point.value,
            unit,
          )}<br/>较前一日：${delta}${n}`;
        },
      },
      xAxis: {
        type: "category",
        data: dates,
        // 20 个点、10px 字号：桌面隔一个显示，窄屏隔三个；`showMaxLabel` 保证最新一天
        // 永远有标签（否则 interval 恰好跳过末点，图上最后一个日期会"消失"）。
        axisLabel: {
          fontSize: 10,
          interval: narrow ? 3 : 1,
          showMaxLabel: true,
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value",
        scale: true,
        minInterval: 1,
        splitNumber: 3,
        axisLabel: { formatter: `{value}${metricAxisSuffix(unit)}` },
      },
      series: [
        {
          type: "line",
          data: points.map((p) => ({
            value: p.value == null || p.lowSample ? null : metricAxisValue(p.value, unit),
            // 点的颜色 = 较前一交易日：红增 / 绿减 / 灰（缺前值）。趋势看线，方向看色。
            // 样本不足的日子用**空心点**：值仍在，但不参与统计。
            itemStyle: p.lowSample
              ? {
                  color: colors.bgPanel,
                  borderColor: colors.textSecondary,
                  borderWidth: 1.4,
                }
              : {
                  color:
                    p.delta == null
                      ? colors.flat
                      : p.delta > 0
                        ? colors.up
                        : p.delta < 0
                          ? colors.down
                          : colors.flat,
                },
          })),
          connectNulls: true,
          showSymbol: true,
          symbolSize: 7,
          lineStyle: { width: 1.6, color: colors.accent },
          endLabel: {
            show: last?.value != null && !last?.lowSample,
            formatter: () => formatMetricDisplay(last?.value, unit),
            color: colors.textPrimary,
            fontSize: 11,
            distance: 4,
          },
        },
        {
          type: "scatter",
          data: lowSamplePoints,
          symbolSize: 6,
          itemStyle: {
            color: "transparent",
            borderColor: colors.textSecondary,
            borderWidth: 1.4,
          },
          z: 4,
        },
      ],
    };
  }, [colors, data.metric.label, narrow, points, unit]);

  const tableRows = useMemo(() => [...points].reverse(), [points]);

  const columns: ColumnsType<(typeof tableRows)[number]> = [
    {
      title: "交易日",
      dataIndex: "tradeDate",
      width: narrow ? 74 : 130,
      render: (value: string) => (
        <span className="num">{narrow ? value.slice(5) : value}</span>
      ),
    },
    {
      title: data.metric.label,
      dataIndex: "value",
      align: "right",
      render: (value: number | null) => (
        <span className="num">{formatMetricDisplay(value, unit)}</span>
      ),
    },
    {
      title: narrow ? "较前值" : "较前一交易日",
      dataIndex: "delta",
      align: "right",
      render: (value: number | null) => (
        <span className={`num ${signedClass(value, data.metric.hotterWhen)}`}>
          {formatMetricDelta(value, unit)}
        </span>
      ),
    },
    ...(narrow
      ? []
      : [
          {
            title: "样本 n",
            dataIndex: "n" as const,
            align: "right" as const,
            width: 90,
            render: (value: number | null, row: { lowSample: boolean }) => (
              <span className={`num ${row.lowSample ? "smd-warn" : "smd-muted"}`}>
                {value == null ? "--" : value}
              </span>
            ),
          },
        ]),
  ];

  return (
    <section className="smd-card" data-testid="sentiment-metric-recent">
      <header className="smd-card__head">
        <h2>近 {points.length} 个交易日</h2>
        <span className="smd-card__note">
          点色 = 较前一交易日：<b className="smd-legend smd-legend--hot">红增</b> /{" "}
          <b className="smd-legend smd-legend--cold">绿减</b> · 空心 = 样本不足
        </span>
      </header>
      <EChart option={option} height={narrow ? 180 : 210} />
      {/* 图是主叙事；精确数字仍可查，但默认收起（不拿表格抢走"一眼看趋势"的注意力）。 */}
      <Collapse
        ghost
        size="small"
        className="smd-details"
        items={[
          {
            key: "table",
            label: <span className="smd-details__label">逐日明细（表格）</span>,
            children: (
              <Table
                size="small"
                rowKey="tradeDate"
                pagination={false}
                columns={columns}
                dataSource={tableRows}
              />
            ),
          },
        ]}
      />
    </section>
  );
}

function CaliberCard({ data }: { data: SentimentMetricDetail }) {
  return (
    <section className="smd-caliber">
      <h2>口径与缺失</h2>
      <p>{data.metric.caliber}</p>
      <p>
        本页所有统计量、分位与直方图都由后端按<strong>有效样本</strong>计算（NULL
        的日子既不进样本也不计 0）；样本不足时整块显示「不给分位 / 不画分布」，而不是给一个看起来像事实的数字。
      </p>
      <p>
        窗口 = `market_sentiment_daily` 最近 {data.window.days} 行（实际 {data.window.samples}{" "}
        个交易日），不插值、不补缺日；数据在盘后任务落库后才会前进到当日。
      </p>
    </section>
  );
}
