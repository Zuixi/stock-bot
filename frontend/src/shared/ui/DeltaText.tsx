import "./DeltaText.css";

export interface DeltaTextProps {
  value: number | null | undefined;
  suffix?: string;
}

/** 涨跌数字：正 `+` / 负 `−` / 零无符号，缺失渲染 `--`（绝不渲染 0.00%）。 */
export function DeltaText({ value, suffix = "%" }: DeltaTextProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="delta delta--flat">--</span>;
  }
  const cls = value > 0 ? "delta--up" : value < 0 ? "delta--down" : "delta--flat";
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return (
    <span className={`delta ${cls}`}>
      {sign}
      {Math.abs(value).toFixed(2)}
      {suffix}
    </span>
  );
}
