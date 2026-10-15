import ReactECharts from "echarts-for-react";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";

interface Props {
  option: Record<string, unknown>;
  height?: number;
  /** 迷你图等无交互场景关闭 tooltip/动画 */
  silent?: boolean;
  /** echarts 事件绑定（如 treemap click），透传给 echarts-for-react */
  onEvents?: Record<string, (params: unknown) => void>;
}

/** 深合并：defaults <- option，option 恒优先；数组按元素位置逐项合并（长度以 option 为准） */
function deepMerge(defaults: Record<string, unknown>, option: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...defaults };
  for (const [key, value] of Object.entries(option)) {
    const prev = out[key];
    if (prev != null && value != null && typeof prev === "object" && typeof value === "object") {
      if (Array.isArray(prev) && Array.isArray(value)) {
        out[key] = value.map((item, i) => {
          const p = prev[i];
          return p != null &&
            item != null &&
            typeof p === "object" &&
            typeof item === "object" &&
            !Array.isArray(p) &&
            !Array.isArray(item)
            ? deepMerge(p as Record<string, unknown>, item as Record<string, unknown>)
            : item;
        });
        continue;
      }
      if (!Array.isArray(prev) && !Array.isArray(value)) {
        out[key] = deepMerge(prev as Record<string, unknown>, value as Record<string, unknown>);
        continue;
      }
    }
    out[key] = value;
  }
  return out;
}

/**
 * ECharts 不消费 CSS var：从 ThemeContext 取当前模式的具体色值注入
 * 全局文本/图例/轴标签/轴线/分隔线默认色，调用方显式设置恒优先。
 */
function withChartTheme(option: Record<string, unknown>, c: ThemePalette): Record<string, unknown> {
  const axisDefaults = {
    axisLabel: { color: c.textSecondary },
    axisLine: { lineStyle: { color: c.border } },
    splitLine: { lineStyle: { color: c.border } },
  };
  const toAxisArray = (v: unknown): unknown[] | undefined =>
    v == null ? undefined : Array.isArray(v) ? v : [v];
  const xAxis = toAxisArray(option.xAxis);
  const yAxis = toAxisArray(option.yAxis);

  const defaults: Record<string, unknown> = {
    // 暗色下透出卡片底色（契约 §4：卡片 --bg-panel 底）
    backgroundColor: "transparent",
    textStyle: { color: c.textSecondary },
    legend: { textStyle: { color: c.textSecondary } },
  };
  const normalized: Record<string, unknown> = { ...option };
  if (xAxis) {
    defaults.xAxis = xAxis.map(() => axisDefaults);
    normalized.xAxis = xAxis; // 统一转数组形态，保证逐轴合并
  }
  if (yAxis) {
    defaults.yAxis = yAxis.map(() => axisDefaults);
    normalized.yAxis = yAxis;
  }
  return deepMerge(defaults, normalized);
}

/** ECharts 统一封装：统一交互默认值，避免每个图表重复 echarts.init 样板。 */
export function EChart({ option, height = 300, silent = false, onEvents }: Props) {
  const { colors } = useTheme();
  const themed = withChartTheme(option, colors);
  const finalOption = silent
    ? { ...themed, animation: false, tooltip: { show: false } }
    : themed;
  return (
    <ReactECharts
      option={finalOption}
      notMerge
      lazyUpdate
      onEvents={onEvents}
      style={{ height, width: "100%" }}
      opts={{ renderer: "canvas" }}
    />
  );
}

/**
 * 按容器宽度下采样序列（Phase 3 §4.1 / §0.1 B2）。
 *
 * **为什么必须有**：sparkline 的旧规则是"`width ≥ 768` 才渲染"——它直接把图在窄屏上
 * **隐藏**，与 owner 的 B2 全量响应式裁定冲突（§0.1）。改为"**始终渲染**，点数按容器宽度定"：
 * 88px 的槽位里画 250 个点，每点 0.35px，画出来是一团墨而不是走势；降到 ~40 点后
 * 形状仍可辨认（迷你图的判据是"趋势可读"，不是"每个数据点都在"）。
 *
 * 取点规则（**首末必留**）：先保留最后一点（"当前值"是迷你图唯一有绝对含义的点），
 * 再按均匀步长回取，最后把首点补上（"起点"定义整段走势的基准）。顺序按原序列返回。
 * 三点以下是退化情形，原样返回——采样器不该改变"数据本来就少"这个事实。
 */
function downsample<T>(data: T[], maxPoints: number): T[] {
  if (maxPoints <= 0 || data.length <= maxPoints) return data;
  const n = data.length;
  const step = (n - 1) / (maxPoints - 1);
  const picked = new Set<number>([n - 1]);
  for (let i = 0; i < maxPoints - 1; i += 1) picked.add(Math.round(i * step));
  return [...picked].sort((a, b) => a - b).map((i) => data[i]);
}

/** 指标迷你走势的公共 option 片段。
 *
 * `maxPoints`：容器宽度决定的最大画点数（不传 = 不采样，保持既有行为）。
 * 由调用方按**容器**宽度算，不按视口断点——同一视口下不同槽位宽度可以差 3 倍。 */
export function sparkOption(
  data: number[],
  color: string,
  maxPoints?: number
): Record<string, unknown> {
  const series = maxPoints == null ? data : downsample(data, maxPoints);
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: "category", show: false },
    yAxis: { type: "value", show: false },
    series: [
      {
        type: "line",
        data: series,
        symbol: "none",
        smooth: 0.35,
        lineStyle: { width: 1.5, color },
        areaStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: `${color}2e` },
              { offset: 1, color: `${color}00` },
            ],
          },
        },
      },
    ],
  };
}
