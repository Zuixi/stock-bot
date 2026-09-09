import "./landing.css";
import { LandingNav } from "./sections/LandingNav";
import { Hero } from "./sections/Hero";
import { MarketPulse } from "./sections/MarketPulse";
import { ValueProps } from "./sections/ValueProps";
import { ProductShowcase } from "./sections/ProductShowcase";
import { DataCoverage } from "./sections/DataCoverage";
import { IndustryGrid } from "./sections/IndustryGrid";
import { AccountPerks } from "./sections/AccountPerks";
import { BottomCTA } from "./sections/BottomCTA";
import { LandingFooter } from "./sections/LandingFooter";

/**
 * StockBot 品牌宣传页（公开路由，独立布局不套 MainLayout，契约 §2/§3）。
 * 品牌主位是 StockBot，猪智投作为首个行业案例露出；
 * 已登录停留此页，导航/Hero/底部 CTA 统一换「进入工作台」→ /market。
 */
export default function LandingPage() {
  return (
    <div className="landing-root">
      <LandingNav />
      <main>
        <Hero />
        <MarketPulse />
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
