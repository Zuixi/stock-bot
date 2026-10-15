import { MarketMoneyflowContent } from "./MarketMoneyflowCard";
import "./MoneySentiment.css";

/**
 * 首页资金与情绪区：仅「大盘资金流」（沪深两市四档），直接内联内容不套 Card。
 *
 * 外层由首页 `<SectionCard id="money">` 提供卡片壳与标题，其它四个行情块同样是
 * 壳内纯内容——若此处再嵌一层 antd `Card` 会渲染成「卡中卡」，与四邻视觉不一致。
 * 子标题沿用板块区（`sector-flow__sub`）同款版式，保持五块同构。
 *
 * 首页不渲染北向卡：`northbound_daily` 现存的 42 行**全部**是 2024-08-19 之后的
 * `north_money`，即**成交额**口径（`amount_kind='turnover'`，不是净流入，见 Ruling 62/63
 * 与 `docs/design/data-source.md` 附.9）；市场页的 `NorthboundCard` 已按成交额口径展示它，
 * 首页不重复、更不以本地序列或"净流入"名义替补。
 * 数据源 `/market/market-moneyflow` 为公开接口，免登录可读。
 */
export function MoneySentiment() {
  return (
    <div className="money-sentiment">
      <div className="money-sentiment__sub">
        <span>大盘资金流</span>
        <span className="money-sentiment__note">沪深两市 · 近30日</span>
      </div>
      <MarketMoneyflowContent />
    </div>
  );
}
