import { useId, useMemo, type ReactNode } from "react";
import type { SentimentCalendarPoint, SentimentKpis } from "@/shared/api/limitUp";
import "./SentimentThermometer.css";

interface Props {
  kpis: SentimentKpis | null | undefined;
  /** `market_sentiment_daily` 历史序列（升序），供环比 chip 与趋势线；<2 点时不画趋势。 */
  history: SentimentCalendarPoint[];
  asOf: string | null | undefined;
}

/**
 * 0-1 比值 → 百分比文案。后端契约里 `broken_rate`/晋级率是比值（0.4062 = 40.62%），
 * 直接 `toFixed(2)%` 会显示成 "0.41%"（历史 bug，重设计时修正）。
 */
function pct(ratio: number | null | undefined): string {
  return ratio == null ? "--" : `${(ratio * 100).toFixed(2)}%`;
}

/** 带符号百分比（数值本身已是 % 口径，如昨日涨停均值 +3.21%）。 */
function signedPct(value: number | null | undefined): string {
  if (value == null) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(2)}%`;
}

/** 与前一交易日的差值 pill；无前值不渲染（缺失 ≠ 0）。计数差取整数，比率差带 % 单位。 */
function DeltaChip({ diff, unit = "" }: { diff: number | null; unit?: string }) {
  if (diff == null) return null;
  const cls = diff > 0 ? "thermo-chip--up" : diff < 0 ? "thermo-chip--down" : "thermo-chip--flat";
  const sign = diff > 0 ? "+" : diff < 0 ? "\u2212" : "";
  return (
    <span className={`thermo-chip ${cls}`} title="较前一交易日">
      {sign}
      {Math.abs(diff).toFixed(unit === "%" ? 2 : 0)}
      {unit}
    </span>
  );
}

function PrimaryTile({
  label,
  count,
  prev,
  tone,
}: {
  label: string;
  count: number;
  prev: number | undefined;
  tone: "up" | "warn" | "down";
}) {
  return (
    <div className={`thermo-tile thermo-tile--${tone}`}>
      <span className="thermo-tile__label">{label}</span>
      <span className="thermo-tile__row">
        <span className="thermo-tile__num">{count}</span>
        <span className="thermo-tile__unit">家</span>
        <DeltaChip diff={prev == null ? null : count - prev} />
      </span>
    </div>
  );
}

/** 多空力量条：涨停/炸板/跌停家数占比一屏读出强弱（零值段隐藏，不画 0 宽假条）。 */
function StrengthBar({ zt, zb, dt }: { zt: number; zb: number; dt: number }) {
  return (
    <div
      className="thermo-bar"
      role="img"
      aria-label={`涨停 ${zt} 家 · 炸板 ${zb} 家 · 跌停 ${dt} 家`}
    >
      {zt > 0 && (
        <span className="thermo-bar__seg thermo-bar__seg--up" style={{ flexGrow: zt }} title={`涨停 ${zt} 家`} />
      )}
      {zb > 0 && (
        <span className="thermo-bar__seg thermo-bar__seg--warn" style={{ flexGrow: zb }} title={`炸板 ${zb} 家`} />
      )}
      {dt > 0 && (
        <span className="thermo-bar__seg thermo-bar__seg--down" style={{ flexGrow: dt }} title={`跌停 ${dt} 家`} />
      )}
    </div>
  );
}

function MetricTile({ label, note, children }: { label: string; note?: ReactNode; children: ReactNode }) {
  return (
    <div className="thermo-tile thermo-tile--metric">
      <span className="thermo-tile__label">{label}</span>
      <div className="thermo-metric">{children}</div>
      {note ? <div className="thermo-tile__note">{note}</div> : null}
    </div>
  );
}

/** 涨停/跌停家数双线趋势（纯 SVG，viewBox 拉伸 + non-scaling-stroke 防线宽变形）。 */
function TrendSpark({
  points,
}: {
  points: Array<Pick<SentimentCalendarPoint, "tradeDate" | "ztCount" | "dtCount">>;
}) {
  const gid = useId();
  const w = 600;
  const h = 64;
  const pad = 4;
  const zs = points.map((p) => p.ztCount);
  const ds = points.map((p) => p.dtCount);
  const max = Math.max(...zs, ...ds, 1);
  const x = (i: number) => pad + (i * (w - 2 * pad)) / (points.length - 1);
  const y = (v: number) => h - pad - (v / max) * (h - 2 * pad);
  const line = (vals: number[]) =>
    vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const area = `${line(zs)} L${x(zs.length - 1).toFixed(1)},${h - pad} L${x(0).toFixed(1)},${h - pad} Z`;
  const last = points[points.length - 1];

  return (
    <div className="thermo-trend">
      <div className="thermo-trend__legend">
        <span className="thermo-trend__key">
          <i className="thermo-trend__dot thermo-trend__dot--up" />
          涨停 {last.ztCount}
        </span>
        <span className="thermo-trend__key">
          <i className="thermo-trend__dot thermo-trend__dot--down" />
          跌停 {last.dtCount}
        </span>
        <span className="thermo-trend__range">近 {points.length} 个交易日</span>
      </div>
      <svg
        className="thermo-trend__svg"
        viewBox={`0 0 ${w} ${h}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`近 ${points.length} 个交易日涨停/跌停家数趋势`}
      >
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: "var(--up)", stopOpacity: 0.2 }} />
            <stop offset="100%" style={{ stopColor: "var(--up)", stopOpacity: 0 }} />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gid})`} />
        <path d={line(zs)} className="thermo-trend__line thermo-trend__line--up" vectorEffect="non-scaling-stroke" />
        <path d={line(ds)} className="thermo-trend__line thermo-trend__line--down" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}

/**
 * 情绪温度计（hero 风）：三大计数大数字 + 多空力量条 + 二级指标瓦片 + 周期趋势线。
 * `kpis` 为 null（罕见：非降级但 KPI 缺失）时整板退化为 `--`，绝不 `?? 0`。
 * 环比与趋势来自 `market_sentiment_daily` 派生缓存，缺失时只藏对应元素、不造假数据。
 */
export function SentimentThermometer({ kpis, history, asOf }: Props) {
  const sorted = useMemo(
    () => [...history].sort((a, b) => a.tradeDate.localeCompare(b.tradeDate)),
    [history],
  );
  const prev = useMemo(() => {
    if (!asOf) return undefined;
    const earlier = sorted.filter((p) => p.tradeDate < asOf);
    return earlier.length > 0 ? earlier[earlier.length - 1] : undefined;
  }, [sorted, asOf]);
  // 日历缓存滞后于 asOf（盘后任务未跑）时，把当日实时 KPI 追加为趋势末点，避免线尾与主卡数字不一致。
  // 注意：useMemo 必须全部位于 `!kpis` 早退之前（条件 hooks 会在数据到达时触发渲染错误）
  const trendSeries = useMemo(() => {
    if (!asOf || !kpis) return sorted;
    const last = sorted[sorted.length - 1];
    if (last && last.tradeDate >= asOf) return sorted;
    return [...sorted, { tradeDate: asOf, ztCount: kpis.ztCount, dtCount: kpis.dtCount }];
  }, [sorted, kpis, asOf]);

  if (!kpis) {
    return (
      <div className="thermo-empty" data-testid="sentiment-thermometer-empty">
        --
      </div>
    );
  }

  const ppDiff = (today: number | null, before: number | null | undefined): number | null =>
    today == null || before == null ? null : (today - before) * 100;

  return (
    <div className="thermo" data-testid="sentiment-thermometer">
      <div className="thermo-primary">
        <PrimaryTile label="涨停家数" count={kpis.ztCount} prev={prev?.ztCount} tone="up" />
        <PrimaryTile label="炸板家数" count={kpis.zbCount} prev={prev?.zbCount} tone="warn" />
        <PrimaryTile label="跌停家数" count={kpis.dtCount} prev={prev?.dtCount} tone="down" />
      </div>
      <StrengthBar zt={kpis.ztCount} zb={kpis.zbCount} dt={kpis.dtCount} />

      <div className="thermo-grid">
        <MetricTile label="炸板率">
          <span className="thermo-metric__num">{pct(kpis.brokenRate)}</span>
          <DeltaChip diff={ppDiff(kpis.brokenRate, prev?.brokenRate ?? null)} unit="%" />
        </MetricTile>
        <MetricTile label="昨日涨停均值" note={kpis.yztN > 0 ? `n=${kpis.yztN}` : undefined}>
          <span
            className={`thermo-metric__num ${
              kpis.yztAvgPct == null
                ? "thermo-metric__num--muted"
                : kpis.yztAvgPct > 0
                  ? "thermo-metric__num--up"
                  : kpis.yztAvgPct < 0
                    ? "thermo-metric__num--down"
                    : ""
            }`}
          >
            {signedPct(kpis.yztAvgPct)}
          </span>
        </MetricTile>
        <MetricTile label="今开溢价均值">
          <span
            className={`thermo-metric__num ${
              kpis.yztAvgOpenPremium == null
                ? "thermo-metric__num--muted"
                : kpis.yztAvgOpenPremium > 0
                  ? "thermo-metric__num--up"
                  : kpis.yztAvgOpenPremium < 0
                    ? "thermo-metric__num--down"
                    : ""
            }`}
          >
            {signedPct(kpis.yztAvgOpenPremium)}
          </span>
        </MetricTile>
        <MetricTile
          label="1进2晋级率"
          note={kpis.promo1to2Noisy ? `n=${kpis.promo1to2N}，样本小` : `n=${kpis.promo1to2N}`}
        >
          <span className={`thermo-metric__num${kpis.promo1to2Noisy ? " thermo-metric__num--muted" : ""}`}>
            {pct(kpis.promo1to2)}
          </span>
          <DeltaChip diff={ppDiff(kpis.promo1to2, prev?.promo1to2 ?? null)} unit="%" />
        </MetricTile>
        <MetricTile
          label="2进3晋级率"
          note={kpis.promo2to3Noisy ? `n=${kpis.promo2to3N}，样本小` : `n=${kpis.promo2to3N}`}
        >
          <span className={`thermo-metric__num${kpis.promo2to3Noisy ? " thermo-metric__num--muted" : ""}`}>
            {pct(kpis.promo2to3)}
          </span>
          <DeltaChip diff={ppDiff(kpis.promo2to3, prev?.promo2to3 ?? null)} unit="%" />
        </MetricTile>
        <MetricTile label="最高板">
          <span className="thermo-metric__num">{kpis.maxStreak}</span>
          <span className="thermo-metric__unit">板</span>
          <DeltaChip
            diff={prev == null ? null : kpis.maxStreak - prev.maxStreak}
          />
        </MetricTile>
      </div>

      {trendSeries.length >= 2 ? (
        <TrendSpark points={trendSeries} />
      ) : (
        <div className="thermo-trend thermo-trend--pending">
          趋势线需 ≥2 个交易日（每日盘后写入情绪周期缓存后自动出现）
        </div>
      )}
    </div>
  );
}
