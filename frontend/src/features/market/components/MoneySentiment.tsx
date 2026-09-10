import { MarketMoneyflowContent } from "./MarketMoneyflowCard";
import "./MoneySentiment.css";

/**
 * 首页资金与情绪区：仅「大盘资金流」（沪深两市四档），直接内联内容不套 Card。
 *
 * 外层由首页 `<SectionCard id="money">` 提供卡片壳与标题，其它四个行情块同样是
 * 壳内纯内容——若此处再嵌一层 antd `Card` 会渲染成「卡中卡」，与四邻视觉不一致。
 * 子标题沿用板块区（`sector-flow__sub`）同款版式，保持五块同构。
 *
 * 北向卡整卡移除：`northbound_daily` 无数据行（非仅滞后），`/market/northbound`
 * 不可用——不以本地序列或其它口径替补，避免在首页展示无源可溯的伪指标。
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
