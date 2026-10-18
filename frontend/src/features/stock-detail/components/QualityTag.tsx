import { Tooltip } from "antd";

/**
 * 财务指标的数据质量徽标（`reported` 财报 / `derived` 估算 + 数据来源）。
 *
 * 从 `FinancialTab` 提为共用组件：概览页「成长与盈利」卡与财务 tab 读的是**同一个**
 * `financial-summary` 响应，质量口径必须逐字一致，否则同一指标会在两处显示不同徽标。
 * 纯展示、无数据依赖——`quality` 为空时什么点/字都不渲染，由调用方决定是否显示。
 */
export function QualityTag({
  quality,
  source,
}: {
  quality?: string | null;
  source?: string | null;
}) {
  const preset =
    quality === "derived"
      ? { color: "#faad14", label: "估算" }
      : quality === "reported"
        ? { color: "#52c41a", label: "财报" }
        : null;
  return (
    <Tooltip title={`质量: ${quality ?? "未知"}${source ? ` · 来源: ${source}` : ""}`}>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
          color: "#999",
        }}
      >
        {preset ? (
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: preset.color,
              display: "inline-block",
            }}
          />
        ) : null}
        {preset?.label}
        {source ? ` · ${source}` : ""}
      </span>
    </Tooltip>
  );
}
