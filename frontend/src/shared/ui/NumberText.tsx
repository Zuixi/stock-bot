import { Tooltip } from "antd";

interface Props {
  value: number | undefined | null;
  unit?: "yuan" | "cap";
  precision?: number;
  style?: React.CSSProperties;
}

function formatCap(v: number): string {
  if (Math.abs(v) >= 1e12) return (v / 1e12).toFixed(2) + "万亿";
  if (Math.abs(v) >= 1e8) return (v / 1e8).toFixed(2) + "亿";
  if (Math.abs(v) >= 1e4) return (v / 1e4).toFixed(2) + "万";
  return v.toLocaleString("zh-CN");
}

export function NumberText({ value, unit, precision = 2, style }: Props) {
  if (value == null) return <span style={{ color: "#9ca3af", ...style }}>--</span>;

  const display = unit === "cap" ? formatCap(value) : value.toFixed(precision);
  const raw = value.toLocaleString("zh-CN", { maximumFractionDigits: 4 });

  return (
    <Tooltip title={raw}>
      <span style={{ fontVariantNumeric: "tabular-nums", ...style }}>{display}</span>
    </Tooltip>
  );
}
