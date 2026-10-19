import { useMemo } from "react";
import "./MarketPanoramaPulse.css";
import { Segmented, Skeleton } from "antd";
import { EChart } from "@/shared/ui/EChart";
import { Sparkline } from "@/shared/ui/Sparkline";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";
import {
  formatAmountQianToYi,
  formatYuanToYi,
  type MarketPanorama,
  type PanoramaBreadthPoint,
  type PanoramaStructurePoint,
} from "@/shared/api/panorama";

const RANGES = [20, 60, 120, 250] as const;

/** 状态 → 颜色：普涨红 / 普跌绿 / 抱团橙 / 结构强调色。 */
function toneColor(tone: string, c: ThemePalette): string {
  if (tone === "hot") return c.up;
  if (tone === "cold") return c.down;
  if (tone === "extreme") return c["regime-hot"];
  return c.accent;
}

function signed(v: number | null | undefined, digits = 2, suffix = ""): string {
  if (v == null || !Number.isFinite(v)) return "—";
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}${suffix}`;
}

function CompassChart({
  series,
  current,
  colors,
}: {
  series: PanoramaStructurePoint[];
  current: PanoramaStructurePoint | null;
  colors: ThemePalette;
}) {
  const option = useMemo(() => {
    const points = series
      .filter((p) => p.upRatio != null && p.top3Share != null)
      .map((p) => ({
        value: [p.upRatio as number, p.top3Share as number],
        date: p.tradeDate,
        label: p.label,
        dispersion: p.dispersion,
      }));
    const last = points.length ? points[points.length - 1].value : [0, 0];
    return {
      animation: false,
      grid: { left: 36, right: 12, top: 10, bottom: 26 },
      xAxis: {
        type: "value",
        min: 0,
        max: 1,
        interval: 0.25,
        axisLabel: { fontSize: 10, formatter: (v: number) => `${Math.round(v * 100)}%` },
        splitLine: { lineStyle: { color: colors.border, opacity: 0.55 } },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      yAxis: {
        type: "value",
        min: 0,
        max: 0.6,
        splitNumber: 4,
        axisLabel: { fontSize: 10, formatter: (v: number) => `${Math.round(v * 100)}%` },
        splitLine: { lineStyle: { color: colors.border, opacity: 0.55 } },
        axisLine: { show: false },
        axisTick: { show: false },
      },
      tooltip: {
        trigger: "item",
        backgroundColor: colors.bgElevated,
        borderColor: colors.border,
        textStyle: { color: colors.textPrimary, fontSize: 11 },
        formatter: (p: { data?: { value?: number[]; date?: string; label?: string; dispersion?: number | null } }) => {
          const d = p.data;
          if (!d?.value) return "";
          const [x, y] = d.value;
          return (
            `${d.date ?? ""} · ${d.label ?? ""}<br/>` +
            `上涨占比：${(x * 100).toFixed(1)}%<br/>` +
            `Top3 集中度：${(y * 100).toFixed(1)}%<br/>` +
            `行业离散度：${d.dispersion == null ? "—" : d.dispersion.toFixed(2)}`
          );
        },
      },
      series: [
        {
          type: "line",
          data: points.map((p) => p.value),
          showSymbol: false,
          silent: true,
          lineStyle: { color: colors.accent, width: 1.2, opacity: 0.35 },
          z: 1,
        },
        {
          type: "scatter",
          data: points.map((p, i) => ({
            ...p,
            symbolSize: i === points.length - 1 ? 9 : 3.5,
            itemStyle: {
              color: i === points.length - 1 ? toneColor(current?.tone ?? "neutral", colors) : colors.accent,
              opacity: i === points.length - 1 ? 1 : 0.35,
              borderColor: colors.bgPanel,
              borderWidth: i === points.length - 1 ? 1.5 : 0,
            },
          })),
          z: 3,
        },
        {
          type: "scatter",
          data: [last],
          symbolSize: 0,
          silent: true,
          markLine: {
            silent: true,
            symbol: "none",
            lineStyle: { color: colors.border, type: "dashed", opacity: 0.75 },
            label: { show: false },
            data: [{ xAxis: 0.5 }, { yAxis: 0.35 }],
          },
        },
      ],
    };
  }, [colors, current?.tone, series]);
  return (
    <div className="pano-compass" data-testid="market-panorama-compass">
      <EChart option={option} height={196} />
    </div>
  );
}

function Kpi({
  label,
  value,
  unit,
  sub,
  delta,
  spark,
  color,
}: {
  label: string;
  value: string;
  unit?: string;
  sub?: React.ReactNode;
  delta?: React.ReactNode;
  spark?: Array<number | null>;
  color: string;
}) {
  return (
    <div className="pano-kpi">
      <div className="pano-kpi__label">{label}</div>
      <div className="pano-kpi__row">
        <span className="pano-kpi__num">
          <span className="pano-kpi__value num" style={{ color }}>
            {value}
          </span>
          {unit ? <span className="pano-kpi__unit">{unit}</span> : null}
        </span>
        {spark ? <Sparkline values={spark} color={color} width={64} height={18} /> : null}
      </div>
      <div className="pano-kpi__sub">
        {delta}
        {sub}
      </div>
    </div>
  );
}

export interface MarketPanoramaPulseProps {
  data?: MarketPanorama;
  isLoading: boolean;
  /** 接口失败：显示"不可用"而不是一份看起来正常的空日表。 */
  isError?: boolean;
  days: number;
  onDaysChange: (days: number) => void;
}

/**
 * 市场结构脉搏：一张「广度 × 集中度」罗盘 + 4 个变化 KPI。
 *
 * 不是第五张卡：罗盘回答"今天是什么结构、过去 60 天怎么走到这里"，KPI 回答
 * "比昨天变了多少"。所有数字来自 `/market/panorama` 日表，不在前端现算。
 */
export function MarketPanoramaPulse({ data, isLoading, isError, days, onDaysChange }: MarketPanoramaPulseProps) {
  const { colors } = useTheme();
  const latest: PanoramaBreadthPoint | null = data?.breadth.latest ?? null;
  const prev = data?.breadth.previous ?? null;
  const current = data?.structure.current ?? null;
  const stateColor = toneColor(current?.tone ?? "neutral", colors);
  const series = data?.breadth.series ?? [];
  const flowTop = data?.flows?.topIn?.[0] ?? null;
  const flowStale =
    data?.flows != null && data.asOf != null && data.flows.asOf < data.asOf;

  const upDelta = latest && prev ? latest.upCount - prev.upCount : null;
  const downDelta = latest && prev ? latest.downCount - prev.downCount : null;
  const amountDelta =
    latest?.amount != null && prev?.amount != null && prev.amount !== 0
      ? ((latest.amount - prev.amount) / prev.amount) * 100
      : null;
  const medianDelta =
    latest?.medianPct != null && prev?.medianPct != null
      ? latest.medianPct - prev.medianPct
      : null;

  const sparkSlice = <T,>(arr: T[]): T[] => arr.slice(-5);
  const upRatioSpark = sparkSlice(series.map((p) => p.upRatio));
  const medianSpark = sparkSlice(series.map((p) => p.medianPct));
  const amountSpark = sparkSlice(series.map((p) => p.amount));

  return (
    <section className="pano" data-testid="market-panorama" aria-label="市场结构脉搏">
      <header className="pano__head">
        <div className="pano__titleblock">
          <div className="pano__eyebrow">
            市场结构 <span>全景脉搏</span>
          </div>
          <div className="pano__state" style={{ color: stateColor }}>
            {isLoading && !data ? "—" : (current?.label ?? "数据不足")}
          </div>
          <div className="pano__state-meta">
            {latest ? (
              <>
                上涨占比 <b className="num">{(latest.upRatio == null ? 0 : latest.upRatio * 100).toFixed(1)}%</b>
                {" · "}Top3 成交额 <b className="num">{current?.top3Share == null ? "—" : (current.top3Share * 100).toFixed(1)}%</b>
                {" · "}离散度 <b className="num">{current?.dispersion == null ? "—" : current.dispersion.toFixed(2)}</b>
              </>
            ) : (
              "等待日表回补"
            )}
          </div>
        </div>
        <div className="pano__controls">
          <Segmented<number>
            size="small"
            value={days}
            onChange={(v) => onDaysChange(v)}
            options={RANGES.map((d) => ({ label: `${d}D`, value: d }))}
          />
          <span className="pano__asof">
            {data?.asOf ? `数据截至 ${data.asOf}` : "—"}
            {data?.stale ? <em className="pano__stale">滞后 {data.lagDays} 天</em> : null}
          </span>
        </div>
      </header>

      {isError && !data ? (
        <div className="pano__error" role="status">
          全景日表暂不可用。这不是“今天没有数据”，请稍后重试；下方卡片仍各自显示自己的口径。
        </div>
      ) : isLoading && !data ? (
        <div className="pano__grid pano__grid--loading" aria-hidden>
          <Skeleton active paragraph={{ rows: 5 }} title={false} />
          <Skeleton active paragraph={{ rows: 5 }} title={false} />
        </div>
      ) : (
        <div className="pano__grid">
          <div className="pano__left">
            <div className="pano__compass-head">
              <span>广度 × 集中度</span>
              <span className="pano__compass-legend">今日点 · 轨迹 {series.length} 日</span>
            </div>
            <CompassChart series={data?.structure.series ?? []} current={current} colors={colors} />
            <div className="pano__compass-axis">
              横轴 上涨占比 · 纵轴 Top3 集中度
            </div>
          </div>
          <div className="pano__kpis">
            <Kpi
              label="上涨 / 下跌"
              value={latest ? `${latest.upCount} / ${latest.downCount}` : "—"}
              unit="只"
              color={latest && latest.upCount >= latest.downCount ? colors.up : colors.down}
              spark={upRatioSpark}
              delta={
                upDelta == null ? null : (
                  <span>
                    较昨日 <b className={upDelta >= 0 ? "up" : "down"}>{signed(upDelta, 0)}</b>
                    {downDelta == null ? null : ` · 下跌 ${signed(downDelta, 0)}`}
                  </span>
                )
              }
            />
            <Kpi
              label="中位涨跌幅"
              value={latest?.medianPct == null ? "—" : signed(latest.medianPct, 2, "%")}
              color={(latest?.medianPct ?? 0) >= 0 ? colors.up : colors.down}
              spark={medianSpark}
              delta={medianDelta == null ? null : <span>较昨日 {signed(medianDelta, 2, "pp")}</span>}
            />
            <Kpi
              label="成交额"
              value={formatAmountQianToYi(latest?.amount ?? null)}
              color={colors.textPrimary}
              spark={amountSpark}
              delta={amountDelta == null ? null : <span>较昨日 {signed(amountDelta, 1, "%")}</span>}
            />
            <Kpi
              label="主力净流入 Top"
              value={flowTop?.mainNetInflow == null ? "—" : formatYuanToYi(flowTop.mainNetInflow)}
              color={flowTop?.mainNetInflow != null && flowTop.mainNetInflow < 0 ? colors.down : colors.up}
              sub={
                flowTop ? (
                  <span className="pano-kpi__flow">
                    {flowTop.name} · {data?.flows?.asOf}
                    {flowStale ? "（非最新）" : ""}
                  </span>
                ) : (
                  <span className="pano-kpi__flow">暂无完整日资金流</span>
                )
              }
            />
          </div>
        </div>
      )}

      <footer className="pano__foot">
        <span>结构状态 = 广度 + Top3 集中度（启发式，不是交易信号）</span>
        <span>
          行业归属：按当前申万快照回溯
          {data?.freshness.flowAsOf ? ` · 资金流完整日 ${data.freshness.flowAsOf}` : ""}
          {data?.freshness.flowAvailableFrom ? `（自 ${data.freshness.flowAvailableFrom.slice(5)} 起）` : ""}
        </span>
      </footer>
    </section>
  );
}
