import { COLORS } from "@/app/theme";
import type { MetricLatest } from "@/shared/api/industryResearch";

interface Props {
  metrics: MetricLatest[];
}

function formatValue(value: number): string {
  return Math.abs(value) >= 10000
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
    : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/** 核心指标速览网格（两列紧凑瓦片），预警标签由后端阈值计算下发 */
export function IndicatorGrid({ metrics }: Props) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 10, marginTop: 12 }}>
      {metrics.map((m) => (
        <div
          key={m.metricKey}
          style={{
            background: "#fafbfc",
            border: "1px solid #f0f2f5",
            borderRadius: 8,
            padding: "9px 12px",
            transition: "border-color .2s",
          }}
        >
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", fontSize: 12, color: "#4e5969" }}>
            <span>{m.name}</span>
            {m.freq && <span style={{ fontSize: 10.5, color: "#86909c" }}>{freqLabel(m.freq)}</span>}
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 4, marginTop: 3 }}>
            <span
              style={{
                fontFamily: '"Bahnschrift","Segoe UI",sans-serif',
                fontWeight: 700,
                fontSize: 18,
                fontVariantNumeric: "tabular-nums",
                color: m.value !== null ? undefined : "#c9cdd4",
              }}
            >
              {m.value !== null ? formatValue(m.value) : "—"}
            </span>
            {m.unit && <span style={{ fontSize: 11, color: "#86909c" }}>{m.unit}</span>}
            <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
              {m.warn && (
                <span
                  style={{
                    fontSize: 10.5,
                    fontWeight: 600,
                    padding: "0 5px",
                    borderRadius: 4,
                    border: `1px solid ${m.warnSeverity === "danger" ? "#ffccc7" : "#ffe58f"}`,
                    background: m.warnSeverity === "danger" ? "#fff1f0" : "#fffbe6",
                    color: m.warnSeverity === "danger" ? "#cf1322" : "#d48806",
                  }}
                >
                  {m.warn}
                </span>
              )}
              {m.delta?.pct !== null && m.delta && m.delta.direction !== "flat" && (
                <span
                  style={{
                    fontFamily: '"Bahnschrift","Segoe UI",sans-serif',
                    fontWeight: 600,
                    fontSize: 12,
                    color: m.delta.direction === "up" ? COLORS.up : COLORS.down,
                  }}
                >
                  {m.delta.direction === "up" ? "▲" : "▼"}
                  {Math.abs(m.delta.pct ?? 0).toFixed(2)}%
                </span>
              )}
            </span>
          </div>
        </div>
      ))}
    </div>
  );
}

function freqLabel(freq: string): string {
  return { daily: "日度", weekly: "周度", monthly: "月度", quarterly: "季度", yearly: "年度" }[freq] ?? freq;
}
