import { Skeleton } from "antd";
import "./landing.css";
import { SectionCard } from "@/shared/ui";
import { RankingMatrix } from "@/features/market/components";
import { LandingNav } from "./sections/LandingNav";
import { CompactHero } from "./sections/CompactHero";
import { MarketPulse } from "./sections/MarketPulse";
import { ValueProps } from "./sections/ValueProps";
import { ProductShowcase } from "./sections/ProductShowcase";
import { DataCoverage } from "./sections/DataCoverage";
import { IndustryGrid } from "./sections/IndustryGrid";
import { AccountPerks } from "./sections/AccountPerks";
import { BottomCTA } from "./sections/BottomCTA";
import { LandingFooter } from "./sections/LandingFooter";

/** 未填充行情区块的骨架屏占位（每个空 SectionCard 显式挂 children） */
function SectionSkeleton() {
  return <Skeleton active paragraph={{ rows: 4 }} />;
}

/**
 * StockBot 公开行情台首页（免登录，独立布局不套 MainLayout，契约 §2/§3）。
 *
 * 产品决策「免登录行情为主」：区块序列为紧凑 Hero → 脉搏/榜单/板块/资金/日历/快讯，
 * 营销区（ValueProps/ProductShowcase）压缩到行情台之后，数据覆盖与行业网格留在尾部。
 * 行情区块逐个 Task 填充，未填充者以骨架屏独立降级。
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
            <SectionCard id="sectors" title="行业与资金" moreHref="/market/category">
              <SectionSkeleton />
            </SectionCard>
            <SectionCard id="money" title="资金与情绪">
              <SectionSkeleton />
            </SectionCard>
            <SectionCard id="calendar" title="日历">
              <SectionSkeleton />
            </SectionCard>
            <SectionCard id="news" title="快讯">
              <SectionSkeleton />
            </SectionCard>
          </div>
        </section>

        <ValueProps />
        <ProductShowcase />
        <DataCoverage />
        <IndustryGrid />
        <AccountPerks />
        <BottomCTA />
      </main>
      <LandingFooter />
    </div>
  );
}
