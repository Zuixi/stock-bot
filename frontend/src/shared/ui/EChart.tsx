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

/** 指标迷你走势的公共 option 片段 */
export function sparkOption(data: number[], color: string): Record<string, unknown> {
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 2, bottom: 0 },
    xAxis: { type: "category", show: false },
    yAxis: { type: "value", show: false },
    series: [
      {
        type: "line",
        data,
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
