import "./DeltaText.css";

export interface DeltaTextProps {
  value: number | null | undefined;
  suffix?: string;
}

/** 涨跌数字：正 `+` / 负 `−` / 零无符号，缺失渲染 `--`（绝不渲染 0.00%）。 */
export function DeltaText({ value, suffix = "%" }: DeltaTextProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="delta delta--flat num">--</span>;
  }
  const cls = value > 0 ? "delta--up" : value < 0 ? "delta--down" : "delta--flat";
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return (
    <span className={`delta ${cls} num`}>
      {sign}
      {Math.abs(value).toFixed(2)}
      {suffix}
    </span>
  );
}

/**
 * 涨跌额（**绝对变动**，三元组 `最新价 → 涨跌额 → 涨跌幅` 的中间成员，见
 * `instrumentTriplet.ts`）。默认不带后缀：单位由列位/表头决定（A 股清单默认元，
 * 把「元」拼进格子在 390px 下会把三元组顶出容器）。
 *
 * 与 `DeltaText` 同款符号（正 `+` / 负 `−`）与配色（复用 `.delta--up/down/flat`），
 * 但根类名**刻意不是** `delta`：`.delta` 是涨跌幅的既有 locator 契约——多条 spec 用
 * `locator(".delta")` 断言**该行唯一命中**（public-homepage 的榜单行与板块行），
 * 三元组里再出现一个 `.delta` 会把它们变成 strict mode violation。
 */
export function DeltaAbsText({ value, suffix = "" }: DeltaTextProps) {
  if (value === null || value === undefined || Number.isNaN(value)) {
    return <span className="delta-abs delta--flat num">--</span>;
  }
  const cls = value > 0 ? "delta--up" : value < 0 ? "delta--down" : "delta--flat";
  const sign = value > 0 ? "+" : value < 0 ? "\u2212" : "";
  return (
    <span className={`delta-abs ${cls} num`}>
      {sign}
      {Math.abs(value).toFixed(2)}
      {suffix}
    </span>
  );
}
