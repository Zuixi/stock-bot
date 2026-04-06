import { COLORS } from "@/app/theme";

interface Props {
  value: number | undefined | null;
  suffix?: string;
  prefix?: string;
  style?: React.CSSProperties;
}

export function ChangeText({ value, suffix = "%", prefix, style }: Props) {
  if (value == null) return <span style={{ color: COLORS.flat, ...style }}>--</span>;

  const color = value > 0 ? COLORS.up : value < 0 ? COLORS.down : COLORS.flat;
  const sign = value > 0 ? "+" : "";

  return (
    <span style={{ color, fontVariantNumeric: "tabular-nums", ...style }}>
      {prefix}
      {sign}
      {value.toFixed(2)}
      {suffix}
    </span>
  );
}
