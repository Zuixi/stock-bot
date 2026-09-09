import { EChart, sparkOption } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import type { MetricLatest } from "@/shared/api/industryResearch";
import { SourceBadge } from "./SourceBadge";

interface Props {
  metric: MetricLatest;
}

function formatValue(value: number, unit: string | null): string {
  if (unit === "元/吨" || Math.abs(value) >= 10000) {
    return value.toLocaleString("zh-CN", { maximumFractionDigits: 0 });
  }
  return value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/** 综合指标带卡片：值 + 环比 + 预警标签 + 数据源徽章 + 迷你走势 */
export function IndicatorCard({ metric }: Props) {
  const { colors } = useTheme();
  const { value, unit, delta, warn, warnSeverity, spark } = metric;
  const hasValue = value !== null;

  return (
    <div
      style={{
        flex: "1 0 220px",
        background: "var(--bg-panel)",
        border: "1px solid var(--border)",
        borderRadius: 10,
        padding: "12px 16px 8px",
        minWidth: 0,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <span style={{ fontSize: 12.5, color: "var(--text-secondary)", fontWeight: 500, whiteSpace: "nowrap" }}>
          {metric.name}
        </span>
        <SourceBadge tier={metric.tier} />
      </div>

      <div style={{ marginTop: 6, display: "flex", alignItems: "baseline", gap: 5 }}>
        <span
          style={{
            fontFamily: '"Bahnschrift","DIN Alternate","Segoe UI",sans-serif',
            fontWeight: 700,
            fontSize: 25,
            lineHeight: 1.1,
            fontVariantNumeric: "tabular-nums",
            color: hasValue ? "var(--text-primary)" : colors.flat,
          }}
        >
          {hasValue ? formatValue(value, unit) : "—"}
        </span>
        {unit && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{unit}</span>}
      </div>

      <div style={{ marginTop: 5, display: "flex", alignItems: "center", gap: 8, minHeight: 22 }}>
        {warn && (
          <span
            style={{
              fontSize: 11,
              fontWeight: 600,
              padding: "1px 6px",
              borderRadius: 4,
              border: `1px solid ${warnSeverity === "danger" ? "#ffccc7" : "#ffe58f"}`,
              background: warnSeverity === "danger" ? "#fff1f0" : "#fffbe6",
              color: warnSeverity === "danger" ? "#cf1322" : "#d48806",
            }}
          >
            {warn}
          </span>
        )}
        {delta && delta.pct !== null && delta.direction !== "flat" && (
          <span
            style={{
              fontFamily: '"Bahnschrift","DIN Alternate","Segoe UI",sans-serif',
              fontWeight: 600,
              fontSize: 12.5,
              color: delta.direction === "up" ? colors.up : colors.down,
            }}
          >
            {delta.direction === "up" ? "▲" : "▼"} {Math.abs(delta.pct).toFixed(2)}%
          </span>
        )}
        {delta && <span style={{ fontSize: 12, color: "var(--text-secondary)" }}>{delta.label}</span>}
      </div>

      {spark && spark.length > 2 && (
        <div style={{ marginTop: 4 }}>
          <EChart option={sparkOption(spark, colors.primary)} height={36} silent />
        </div>
      )}
    </div>
  );
}
