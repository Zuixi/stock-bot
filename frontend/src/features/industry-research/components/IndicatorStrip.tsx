import { Tooltip } from "antd";
import type { MetricLatest } from "@/shared/api/industryResearch";
import { IndicatorCard } from "./IndicatorCard";

interface Props {
  metrics: MetricLatest[];
}

const LEGEND_TIPS = [
  { color: "#b8d4ff", label: "官方基准", tip: "农业农村部/发改委/统计局口径，分歧时以官方为准" },
  { color: "#d5dbe3", label: "高频参考", tip: "市场化高频数据，用于跟踪边际变化" },
  { color: "#ddd6fe", label: "测算/派生", tip: "基于公开数据推算或由基础指标计算" },
];

/** 综合指标带（横向滚动）+ 数据源分级图例 */
export function IndicatorStrip({ metrics }: Props) {
  return (
    <section>
      <div
        style={{
          display: "flex",
          alignItems: "baseline",
          gap: 12,
          margin: "4px 4px 10px",
        }}
      >
        <h2 style={{ fontSize: 15, fontWeight: 600, display: "flex", alignItems: "center", gap: 8, margin: 0 }}>
          <span style={{ width: 3, height: 14, borderRadius: 2, background: "#1677ff", display: "inline-block" }} />
          综合指标
        </h2>
        <div style={{ marginLeft: "auto", display: "flex", gap: 14, fontSize: 11.5, color: "#86909c" }}>
          {LEGEND_TIPS.map((l) => (
            <Tooltip key={l.label} title={l.tip}>
              <span style={{ display: "inline-flex", alignItems: "center", gap: 5, cursor: "help" }}>
                <i style={{ width: 8, height: 8, borderRadius: 2, background: l.color, display: "inline-block" }} />
                {l.label}
              </span>
            </Tooltip>
          ))}
        </div>
      </div>
      <div style={{ display: "flex", gap: 12, overflowX: "auto", paddingBottom: 4 }}>
        {metrics.map((m) => (
          <IndicatorCard key={m.metricKey} metric={m} />
        ))}
      </div>
    </section>
  );
}
