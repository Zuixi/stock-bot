import { Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useTheme } from "@/app/theme-context";
import { NumberText, useIsNarrowViewport } from "@/shared/ui";
import type { StockRecord } from "@/shared/types";

type ValueKind = "price" | "cap" | "percent";

interface MetricDef {
  label: string;
  /** 取 `StockRecord` 上的原始数值（`undefined` / `null` 一律渲染 `--`，缺失 ≠ 0） */
  read: (stock: StockRecord) => number | undefined;
  kind: ValueKind;
  /** 有公式可解释的指标才给 12px ⓘ（设计稿 §4），其余不挂 tooltip */
  tip?: string;
}

/**
 * 行 3 的八个事实（顺序即设计稿 §2 的「今开 最高 最低 昨收 成交量 成交额 换手率 总市值」）。
 *
 * 标签与值**同行内联**（`标签 值`），不用上下两行：八个事实要在同一组列上竖着扫，
 * 上下两行会让每个格子的行高翻倍、把整条推出 96px 预算。
 */
const METRICS: MetricDef[] = [
  { label: "今开", read: (s) => s.open, kind: "price" },
  { label: "最高", read: (s) => s.high, kind: "price" },
  { label: "最低", read: (s) => s.low, kind: "price" },
  { label: "昨收", read: (s) => s.prevClose, kind: "price" },
  { label: "成交量", read: (s) => s.volume, kind: "cap" },
  { label: "成交额", read: (s) => s.turnover, kind: "cap" },
  {
    label: "换手率",
    read: (s) => s.turnoverRate,
    kind: "percent",
    tip: "成交量 / 流通股本",
  },
  { label: "总市值", read: (s) => s.marketCap, kind: "cap", tip: "总股本 × 最新价" },
];

/** 行 3：八项事实的等宽列栅格（8 列 / ≥768 中屏 4 列，见 QuoteBar.css）。 */
export function QuoteMetricRow({ stock }: { stock: StockRecord }) {
  const { colors } = useTheme();
  /**
   * 窄屏降一档字号（13/12 → 11/11）。
   *
   * 390px 下 4 列每格约 85px，13px 的「成交量 3.85万」需要约 86px ⇒ 会走到 `text-overflow`
   * 把单位截掉（「成交量 3.85…」＝读数缺半句，比换字号严重得多）。11px 仍在既有字号档内，
   * 且此时每格约 79px，八项全部完整可读。
   */
  const narrow = useIsNarrowViewport();
  const labelSize = narrow ? 11 : 12;
  const valueSize = narrow ? 11 : 13;

  return (
    <div className="quote-bar__metrics" data-testid="quote-metric-row">
      {METRICS.map((metric) => {
        const value = metric.read(stock);
        return (
          <div className="quote-bar__metric" key={metric.label} data-metric={metric.label}>
            <span className="quote-bar__metric-label" style={{ fontSize: labelSize }}>
              {metric.label}
              {metric.tip && (
                <Tooltip title={metric.tip}>
                  <InfoCircleOutlined
                    style={{ marginLeft: 2, fontSize: labelSize, color: colors.textSecondary }}
                  />
                </Tooltip>
              )}
            </span>
            {value == null ? (
              // 缺失（不是 0：不得渲染成 0.00）
              <span
                className="quote-bar__metric-value"
                style={{ color: colors.textSecondary, fontSize: valueSize }}
              >
                --
              </span>
            ) : metric.kind === "cap" ? (
              <span className="quote-bar__metric-value">
                <NumberText value={value} unit="cap" style={{ fontSize: valueSize, fontWeight: 500 }} />
              </span>
            ) : (
              <span className="quote-bar__metric-value num" style={{ fontSize: valueSize }}>
                {metric.kind === "percent" ? `${value.toFixed(2)}%` : value.toFixed(2)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
