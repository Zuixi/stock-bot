import "./landing.css";
import { SectionCard } from "@/shared/ui";
import { RankingMatrix } from "@/features/market/components/RankingMatrix";
import { SectorFlow } from "@/features/market/components/SectorFlow";
import { MoneySentiment } from "@/features/market/components/MoneySentiment";
import { MarketNewsFeed } from "@/features/market/components/MarketNewsFeed";
import { LandingNav } from "./sections/LandingNav";
import { CompactHero } from "./sections/CompactHero";
import { MarketPulse } from "./sections/MarketPulse";
import { ValueProps } from "./sections/ValueProps";
import { ProductShowcase } from "./sections/ProductShowcase";
import { IndustryGrid } from "./sections/IndustryGrid";
import { AccountPerks } from "./sections/AccountPerks";
import { BottomCTA } from "./sections/BottomCTA";
import { LandingFooter } from "./sections/LandingFooter";

/**
 * StockBot 公开行情台首页（免登录，独立布局不套 MainLayout，契约 §2/§3）。
 *
 * 产品决策「免登录行情为主」（owner 裁定 D1，plan §0-D）：**行情的 5 个区块全部保留、
 * 不删不迁**。区块序列：紧凑 Hero → 脉搏 / 榜单 / 板块 / 资金 / 快讯。
 * 营销区（ValueProps/ProductShowcase/IndustryGrid/AccountPerks）留在行情台之后。
 *
 * **Phase 4 §5.2 的两处改动**：
 * - **删掉 `#calendar` 区块**：D1 的不删区块是对「区块」说的，而 `日历` 里只有一个
 *   `日历即将上线` 的空占位盒（虚线框 + 48px 内边距，183px 高）——判决书明确把这条例外
 *   出来（plan §0.1 末条："那不是区块，是空盒子"）。空盒子在任何首页定位下都是缺陷，
 *   与 D1/D2 无关。它本来也没有导航入口（`LandingNav` 的 5 个锚点里就没有 calendar）。
 * - 其余 4 个行情区块与营销区**一律不动**，压缩只靠版式（§5.3 的三列密排 + §4.1 间距刻度）。
 *
 * 行情区块逐个 Task 填充，未填充者以显式文案独立降级（不用永久骨架屏——无文案的
 * 持续 shimmer 会被读成页面卡死）。
 */
export default function LandingPage() {
  return (
    <div className="landing-root">
      <LandingNav />
      <main>
        <CompactHero />

        {/* 行情台主体 —— 全部公开。区块间独立降级，单接口失败不牵连邻区。 */}
        <section className="landing-market">
          <div className="landing-container landing-market-inner">
            <SectionCard id="pulse" title="实时市场脉搏" moreHref="/market" moreText="进入行情页">
              <MarketPulse />
            </SectionCard>
            <RankingMatrix />
            <SectionCard id="sectors" title="行业与资金" moreHref="/market/industry">
              <SectorFlow />
            </SectionCard>
            <SectionCard id="money" title="资金与情绪">
              <MoneySentiment />
            </SectionCard>
            {/* 排序：plan §5.2 要求「信息量最低的区块排到最后」。**快讯原本就在最后**
                （改前的序列是 …资金 → 日历 → 快讯），故这条无需再动；真正占着"倒数第二"
                的是那个空日历盒，已删除。 */}
            <SectionCard id="news" title="快讯">
              <MarketNewsFeed />
            </SectionCard>
          </div>
        </section>

        <ValueProps />
        <ProductShowcase />
        <IndustryGrid />
        <AccountPerks />
        <BottomCTA />
      </main>
      <LandingFooter />
    </div>
  );
}
