import ReactECharts from "echarts-for-react";

interface Props {
  option: Record<string, unknown>;
  height?: number;
  /** 迷你图等无交互场景关闭 tooltip/动画 */
  silent?: boolean;
}

/** ECharts 统一封装：统一交互默认值，避免每个图表重复 echarts.init 样板。 */
export function EChart({ option, height = 300, silent = false }: Props) {
  const finalOption = silent
    ? { ...option, animation: false, tooltip: { show: false } }
    : option;
  return (
    <ReactECharts
      option={finalOption}
      notMerge
      lazyUpdate
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
