import { Button, Tag, Tooltip } from "antd";
import { StarFilled, StarOutlined } from "@ant-design/icons";
import { useTheme } from "@/app/theme-context";
import { ChangeText, useIsNarrowViewport } from "@/shared/ui";
import { EXCHANGE_LABELS } from "@/shared/types";
import { useWatchlist } from "@/features/watchlist/useWatchlist";
import type { StockRecord } from "@/shared/types";
import type { PeTtmPercentileState } from "../hooks/usePeTtmPercentile";
import type { StockRangeStatsState } from "../hooks/useStockRangeStats";
import { JournalEntryAction } from "./JournalEntryAction";
import { PositionStrip } from "./PositionStrip";
import { QuoteMetricRow } from "./QuoteMetricRow";
import "./QuoteBar.css";

export interface QuoteBarProps {
  stock: StockRecord;
  /** 52 周区间统计（来自 `useStockRangeStats`，零新增请求） */
  range: StockRangeStatsState;
  /** PE_TTM 3 年分位（来自 `usePeTtmPercentile`，与估值 tab 同一份缓存） */
  percentile: PeTtmPercentileState;
}

/**
 * 报价条：设计稿 §2 的四行布局（身份 / 价格 / 八项事实 / 区间与分位）。
 *
 * 为什么不是卡片墙（设计稿 §6）：一屏三段式里，报价条只负责"它现在什么状态"这一句，
 * 四行 96px 内说完；数值一律 `.num`，缺失一律 `--`，失败一律「重试」。
 *
 * 行 3 走 `QuoteMetricRow`（grid 对齐），行 4 走 `PositionStrip`（区间条 + 分位 + 数据截至），
 * 两者拆成独立文件是为了让每块的可读长度都在一屏内。
 */
export function QuoteBar({ stock, range, percentile }: QuoteBarProps) {
  const { colors } = useTheme();
  const narrow = useIsNarrowViewport();
  const { items, toggle, isMutating } = useWatchlist();
  const isWatched = items.includes(stock.symbol);

  // 现价/涨跌幅/涨跌额三处同色：涨红、跌绿、平（含缺失）中性
  const changePercent = stock.changePercent;
  const trendColor =
    changePercent == null || changePercent === 0
      ? colors.flat
      : changePercent > 0
        ? colors.up
        : colors.down;

  // 窄屏把涨跌额折进 tooltip（设计稿 §2「窄屏」：不占位），宽屏照常内联
  const changeAmount =
    stock.change == null ? "--" : `${stock.change > 0 ? "+" : ""}${stock.change.toFixed(2)}`;

  return (
    <div className="quote-bar" data-testid="quote-bar">
      {/* 行 1：身份 + 动作 */}
      <div className="quote-bar__identity">
        <span className="quote-bar__name">{stock.name}</span>
        <span className="quote-bar__symbol num">{stock.symbol}</span>
        <Tag className="quote-bar__tag">{EXCHANGE_LABELS[stock.exchange]}</Tag>
        {stock.industry && (
          <Tag className="quote-bar__tag" color="blue">
            {stock.industry}
          </Tag>
        )}
        <div className="quote-bar__actions">
          {/* 动作区顺序固定为 [记一笔 default] [加入自选 primary]（设计稿 §2 行 1）：
              自选是这一条上唯一的「主操作」，记一笔是次级入口，故不给它 primary。 */}
          <JournalEntryAction symbol={stock.symbol} />
          <Button
            size="small"
            type={isWatched ? "primary" : "default"}
            icon={isWatched ? <StarFilled /> : <StarOutlined />}
            loading={isMutating}
            onClick={() => toggle(stock.symbol)}
          >
            {isWatched ? "已自选" : "加入自选"}
          </Button>
        </div>
      </div>

      {/* 行 2：现价 / 涨跌幅 / 涨跌额（baseline 对齐） */}
      <div className="quote-bar__price">
        <span className="num quote-bar__last" style={{ color: trendColor }}>
          {stock.latestPrice == null ? "--" : stock.latestPrice.toFixed(2)}
        </span>
        {narrow ? (
          // 窄屏：涨跌额不占位，折进涨跌幅的 tooltip
          <Tooltip title={`涨跌额 ${changeAmount}`}>
            <span>
              <ChangeText value={changePercent} style={{ fontSize: 16, fontWeight: 600 }} />
            </span>
          </Tooltip>
        ) : (
          <>
            <ChangeText value={changePercent} style={{ fontSize: 16, fontWeight: 600 }} />
            <ChangeText value={stock.change} suffix="" style={{ fontSize: 13 }} />
          </>
        )}
      </div>

      {/* 行 3：八个事实 */}
      <QuoteMetricRow stock={stock} />

      {/* 行 4：52 周区间 + 估值分位 + 数据截至 */}
      <PositionStrip
        range={range}
        percentile={percentile}
        latestQuoteDate={stock.latestQuoteDate}
      />
    </div>
  );
}
