// `QualityTag` 刻意**不从本 barrel 导出**：它只有 `FinancialTab` / `FundamentalCards`
// 两个消费者，两者都在本目录内、直接 `./QualityTag` 引入即可。加了 barrel 导出却无人
// 从 barrel 取（本目录的成员若反过来从 `.` 取会形成 import 环），只会留下一条死导出。
// 同理，报价条的三个内部分片（QuoteBar / QuoteMetricRow / PositionStrip / RangeRibbon）
// 只在 `StockHeader.tsx` 这一层被组装，不对外导出；`JournalEntryAction` 同理
// （只被 `QuoteBar` 的动作区消费）。
export { StockHeader } from "./StockHeader";
export { StockTagRow } from "./StockTagRow";
export { FundamentalCards } from "./FundamentalCards";
export { FinancialTab } from "./FinancialTab";
export { ValuationTab } from "./ValuationTab";
export { CustomSwTags } from "./CustomSwTags";
export { UserTags } from "./UserTags";
export { RelatedEvents } from "./RelatedEvents";
// P2-3：侧栏的两块（页面直接消费，故进 barrel）
export { CompanyProfile } from "./CompanyProfile";
export { CharacterFacts } from "./CharacterFacts";
