import { useTheme } from "@/app/theme-context";

interface Props {
  value: number | undefined | null;
  suffix?: string;
  prefix?: string;
  style?: React.CSSProperties;
}

/** 涨跌幅统一渲染：红涨绿跌随主题切换（Stage A：全局件接入 useTheme） */
export function ChangeText({ value, suffix = "%", prefix, style }: Props) {
  const { colors } = useTheme();
  if (value == null) return <span style={{ color: colors.flat, ...style }}>--</span>;

  const color = value > 0 ? colors.up : value < 0 ? colors.down : colors.flat;
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
