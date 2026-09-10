import { MarketMoneyflowCard } from "./MarketMoneyflowCard";

/**
 * 首页资金与情绪区：仅「大盘资金流」卡（沪深两市四档）。
 *
 * 北向卡整卡移除：`northbound_daily` 无数据行（非仅滞后），`/market/northbound`
 * 不可用——不以本地序列或其它口径替补，避免在首页展示无源可溯的伪指标。
 * 数据源 `/market/market-moneyflow` 为公开接口，免登录可读。
 */
export function MoneySentiment() {
  return (
    <div className="money-sentiment">
      <MarketMoneyflowCard />
    </div>
  );
}
