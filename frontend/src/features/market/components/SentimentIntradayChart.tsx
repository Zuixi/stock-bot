import { useId, useMemo } from "react";
import type { SentimentIntradayPoint } from "@/shared/api/limitUp";
import "./SentimentIntradayChart.css";

interface Props {
  /** 盘中分时点（后端已按 captured_at 升序；组件仍按时间戳兜底排序）。 */
  points: SentimentIntradayPoint[];
}

/** viewBox 尺寸（与 `SentimentThermometer.TrendSpark` 同款拉伸画法，非像素尺寸）。 */
const W = 600;
const H = 72;
const PAD = 6;

/** `captured_at` → 上海时区 `HH:MM`（盘中的 X 轴语义是时刻，不是日期）。 */
const SH_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "Asia/Shanghai",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function fmtTime(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "--:--" : SH_TIME.format(d);
}

/**
 * 盘中涨停家数分时曲线（纯 SVG，无图表库、无动画）。
 *
 * 契约：
 * - **0/1 点不画线**——单点采样没有「趋势」可言，退化到带说明的空态（`<2` 早退，
 *   也顺手把 `points.length - 1 === 0` 的除法挡在渲染之外）。
 * - **y 域钳制**——`max === min`（整段涨停家数不变）时 `(v-min)/span` 会 0/0 = NaN，
 *   路径变成 `MNaN,NaN`；此时把线画在中线（见 `y()` 的 span===0 分支）。
 * - 可访问性：`role="img"` + `aria-label` 概括序列（另附 `<title>`），不靠颜色单独传意。
 */
export function SentimentIntradayChart({ points }: Props) {
  const gid = useId();
  const sorted = useMemo(
    () => [...points].sort((a, b) => Date.parse(a.capturedAt) - Date.parse(b.capturedAt)),
    [points],
  );

  if (sorted.length < 2) {
    return (
      <div className="intraday intraday--pending" data-testid="sentiment-intraday-empty">
        {sorted.length === 0
          ? "暂无盘中分时点（交易时段每 5 分钟采集一次）"
          : "分时曲线需 ≥2 个采样点，当前仅 1 点"}
      </div>
    );
  }

  const ys = sorted.map((p) => p.ztCount);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min;
  const x = (i: number) => PAD + (i * (W - 2 * PAD)) / (sorted.length - 1);
  // span === 0 → 中线；否则按 min..max 归一（下方留 PAD 基线，面积填充用得到）
  const y = (v: number) => (span === 0 ? H / 2 : H - PAD - ((v - min) / span) * (H - 2 * PAD));
  const line = sorted
    .map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p.ztCount).toFixed(1)}`)
    .join(" ");
  const area = `${line} L${x(sorted.length - 1).toFixed(1)},${H - PAD} L${x(0).toFixed(1)},${H - PAD} Z`;

  const first = sorted[0];
  const last = sorted[sorted.length - 1];
  const range = span === 0 ? `恒 ${max} 家` : `${min}–${max} 家`;

  return (
    <div className="intraday" data-testid="sentiment-intraday-chart">
      <div className="intraday__legend">
        <span className="intraday__key">
          <i className="intraday__dot" />
          涨停 {last.ztCount}
        </span>
        <span className="intraday__note">
          {fmtTime(first.capturedAt)}–{fmtTime(last.capturedAt)} · {sorted.length} 点 · {range}
        </span>
      </div>
      <svg
        className="intraday__svg"
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        role="img"
        aria-label={`盘中涨停家数分时：${fmtTime(first.capturedAt)} 至 ${fmtTime(last.capturedAt)} 共 ${sorted.length} 个采样点，区间 ${range}，最新 ${last.ztCount} 家`}
      >
        <title>
          盘中涨停家数分时（{sorted.length} 个采样点，最新 {last.ztCount} 家）
        </title>
        <defs>
          <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" style={{ stopColor: "var(--up)", stopOpacity: 0.18 }} />
            <stop offset="100%" style={{ stopColor: "var(--up)", stopOpacity: 0 }} />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gid})`} />
        {/* non-scaling-stroke：viewBox 被 100% 拉伸时线宽不跟着变形（温度计趋势线同法） */}
        <path d={line} className="intraday__line" vectorEffect="non-scaling-stroke" />
      </svg>
    </div>
  );
}
