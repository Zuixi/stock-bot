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

/** 距高/距低是**距离**不是涨跌：带符号、单小数；不可算时返回 `null`（由渲染层写成 `--`）。 */
function formatDistance(value: number | null | undefined): string | null {
  if (value == null || !Number.isFinite(value)) return null;
  const sign = value > 0 ? "+" : "";
  return `${sign}${value.toFixed(1)}%`;
}

/**
 * 3 年分位文案与估值 tab 的 `SummaryStat` **逐字一致**（同一份数据、同一个格式化：
 * `toLocaleString` 最多两位小数），否则同一页两处数字对不上会被读成"两个指标"。
 */
function formatPercentile(value: number | null | undefined): string | null {
  if (value == null || Number.isNaN(value)) return null;
  return `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}%`;
}

/**
 * 行 4 的读数格：**只有真数字**用主文本色，`--`（缺失/加载中）用次级色。
 *
 * 设计稿 §3 的三态里，"没有数字"和"有数字"必须在颜色上也分开 —— 否则 `--` 会和真实读数
 * 一样重，扫一眼分不出"这格是 62%"还是"这格没有值"。故空值走 `--empty` 修饰类。
 */
function StripValue({ text }: { text: string | null }) {
  return (
    <span className={`num quote-bar__strip-value${text == null ? " quote-bar__strip-value--empty" : ""}`}>
      {text ?? "--"}
    </span>
  );
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
        <StripValue text={formatDistance(range.stats?.distanceToHigh)} />
      </span>
      {range.stats && <RangeRibbon stats={range.stats} />}
      <span className="quote-bar__strip-item">
        距低
        <StripValue text={formatDistance(range.stats?.distanceToLow)} />
      </span>
      <span className="quote-bar__strip-item">
        PE_TTM 3年分位
        <StripValue text={formatPercentile(percentile.value)} />
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
