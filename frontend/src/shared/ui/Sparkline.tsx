import { useId } from "react";

export interface SparklineProps {
  values: Array<number | null>;
  width?: number;
  height?: number;
  color: string;
  /** 线宽；默认 1.5 */
  strokeWidth?: number;
  /** 是否在最后一个有效点画圆点 */
  showEndDot?: boolean;
}

/**
 * 纯 SVG 迷你走势：不引 ECharts、不注册实例、不占 ResizeObserver。
 *
 * 一张全景卡里有 4 条 5 日 sparkline；用 4 个 ECharts 实例会带来 4 套 canvas /
 * 事件 / 主题同步，滚动时反而卡。这里只画 polyline，缺失点**断开**（不插值），
 * 有效点 < 2 时画一条水平占位线。
 */
export function Sparkline({
  values,
  width = 72,
  height = 20,
  color,
  strokeWidth = 1.5,
  showEndDot = true,
}: SparklineProps) {
  const clipId = useId().replace(/:/g, "");
  const pts = values
    .map((v, i) => ({ v, i }))
    .filter((p): p is { v: number; i: number } => p.v != null && Number.isFinite(p.v));
  if (pts.length < 2) {
    const y = height / 2;
    return (
      <svg width={width} height={height} aria-hidden="true" focusable="false">
        <line x1={0} y1={y} x2={width} y2={y} stroke={color} strokeOpacity={0.25} strokeWidth={1} />
      </svg>
    );
  }
  const min = Math.min(...pts.map((p) => p.v));
  const max = Math.max(...pts.map((p) => p.v));
  const span = max - min || 1;
  const pad = strokeWidth + 1;
  const x = (i: number) => (values.length <= 1 ? 0 : (i / (values.length - 1)) * (width - pad * 2) + pad);
  const y = (v: number) => height - pad - ((v - min) / span) * (height - pad * 2);
  // 按原始 index 分段：缺失点断开，不要让一条线"跨过"缺失日。
  const segments: string[] = [];
  let current: string[] = [];
  values.forEach((v, i) => {
    if (v == null || !Number.isFinite(v)) {
      if (current.length) segments.push(current.join(" "));
      current = [];
      return;
    }
    current.push(`${x(i).toFixed(2)},${y(v).toFixed(2)}`);
  });
  if (current.length) segments.push(current.join(" "));
  const last = pts[pts.length - 1];
  return (
    <svg width={width} height={height} aria-hidden="true" focusable="false">
      <defs>
        <clipPath id={clipId}>
          <rect x={0} y={0} width={width} height={height} />
        </clipPath>
      </defs>
      <g clipPath={`url(#${clipId})`}>
        {segments.map((points, i) => (
          <polyline
            key={i}
            points={points}
            fill="none"
            stroke={color}
            strokeWidth={strokeWidth}
            strokeLinecap="round"
            strokeLinejoin="round"
            opacity={0.9}
          />
        ))}
        {showEndDot ? <circle cx={x(last.i)} cy={y(last.v)} r={1.8} fill={color} /> : null}
      </g>
    </svg>
  );
}
