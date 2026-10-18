import { formatCnDate } from "@/shared/ui";
import type { PeTtmPercentileState } from "../hooks/usePeTtmPercentile";
import type { StockRangeStatsState } from "../hooks/useStockRangeStats";
import { RangeRibbon } from "./RangeRibbon";

interface Props {
  /** 52 周区间统计（含失败态与重试） */
  range: StockRangeStatsState;
  /** PE_TTM 3 年分位（与估值 tab 同一份缓存） */
  percentile: PeTtmPercentileState;
  /** 行情口径的「数据截至」= `stock.latestQuoteDate`（名录 asof 是另一条管道，不得混用） */
  latestQuoteDate?: string;
}

/** 距高/距低是**距离**不是涨跌：带符号、单小数，缺失渲染 `--`（不渲染成 0.0%）。 */
function formatDistance(value: number | null | undefined): string {
  if (value == null || !Number.isFinite(value)) return "--";
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

/**
 * 3 年分位文案与估值 tab 的 `SummaryStat` **逐字一致**（同一份数据、同一个格式化：
 * `toLocaleString` 最多两位小数），否则同一页两处数字对不上会被读成"两个指标"。
 */
function formatPercentile(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "--";
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
}

/**
 * 行 4：52 周距离 + 区间条 + `PE_TTM 3年分位` + 右对齐「数据截至」。
 *
 * 三态（设计稿 §3，本页最容易被做错的地方）：
 * - 加载中 / 无数据 → 对应片段 `--`（**不整条消失**，避免布局跳动）；
 * - 取数失败 → 行末一枚 12px「加载失败 · 重试」，**绝不**退化成"无数据"的样子；
 * - 「数据截至」始终可见，它不依赖上面两个查询。
 */
export function PositionStrip({ range, percentile, latestQuoteDate }: Props) {
  const failed = range.isError || percentile.isError;
  const retry = () => {
    if (range.isError) range.retry();
    if (percentile.isError) percentile.retry();
  };

  return (
    <div className="quote-bar__strip" data-testid="quote-position-strip">
      <span className="quote-bar__strip-item">
        距高
        <span className="num quote-bar__strip-value">
          {formatDistance(range.stats?.distanceToHigh)}
        </span>
      </span>
      {range.stats && <RangeRibbon stats={range.stats} />}
      <span className="quote-bar__strip-item">
        距低
        <span className="num quote-bar__strip-value">
          {formatDistance(range.stats?.distanceToLow)}
        </span>
      </span>
      <span className="quote-bar__strip-item">
        PE_TTM 3年分位
        <span className="num quote-bar__strip-value">{formatPercentile(percentile.value)}</span>
      </span>
      <div className="quote-bar__strip-tail">
        {failed && (
          <button type="button" className="quote-bar__retry" onClick={retry}>
            加载失败 · 重试
          </button>
        )}
        <span className="quote-bar__asof" data-testid="quote-asof">
          数据截至 {latestQuoteDate ? formatCnDate(latestQuoteDate) : "--"}
        </span>
      </div>
    </div>
  );
}
