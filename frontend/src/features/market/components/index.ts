export { GlobalMarketBoard } from "./GlobalMarketBoard";
export { GlobalIndexCardView } from "./GlobalIndexCardView";
export { DataSourceBadge, PriceSourceBadge } from "./DataSourceBadge";
export { CoreIndexCards } from "./CoreIndexCards";
export { CORE_TS_CODES, pickCoreIndices } from "./coreIndices";
export { DistributionChart } from "./DistributionChart";
export { DistributionBars } from "./DistributionBars";
export type { DistributionBucket } from "./DistributionBars";
export { BreadthBar } from "./BreadthBar";
export type { BreadthBarProps } from "./BreadthBar";
export { SectorHeatmap } from "./SectorHeatmap";
export { SectorMoneyflowCard } from "./SectorMoneyflowCard";
export { MarketMoneyflowCard, MarketMoneyflowContent } from "./MarketMoneyflowCard";
export { NorthboundCard } from "./NorthboundCard";
export { HotSectors } from "./HotSectors";
export { BoardDrilldownDrawer } from "./BoardDrilldownDrawer";
export type { BoardDrilldownTarget } from "./BoardDrilldownDrawer";
// `IndustryClassification` 已按 Phase 1 §W2.2 删除（Ruling 33：无人渲染 = 死代码）：
// 它与 `SwIndustryGrid` 同屏渲染同一批 31 个行业，重复的那份去掉后不再有渲染方。
// 注意**不是**删能力：它承载的「N 只 / M 个二级」已并进 `SwIndustryGrid` 每格第二行，
// 而 7 项行情聚合条 `IndustryMetricStrip` 仍由各下钻页渲染（本文件下方不导出它，
// 下钻页直接从 `./IndustryMetricStrip` 取）。
export { MarketDataBoard } from "./MarketDataBoard";
export { SwIndustryGrid } from "./SwIndustryGrid";
export { RankingMatrix } from "./RankingMatrix";
export { SectorFlow } from "./SectorFlow";
export { MoneySentiment } from "./MoneySentiment";
export { MarketNewsFeed } from "./MarketNewsFeed";
export { AnnouncementFeed } from "./dataFace/AnnouncementFeed";
export type { AnnouncementFeedProps } from "./dataFace/AnnouncementFeed";
export { SentimentThermometer } from "./SentimentThermometer";
export { SentimentIntradayChart } from "./SentimentIntradayChart";
export { LimitUpLadder } from "./LimitUpLadder";
export { SwL3LimitUpBoard } from "./SwL3LimitUpBoard";
export { YesterdayLimitUp } from "./YesterdayLimitUp";
