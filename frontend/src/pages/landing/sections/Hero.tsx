import { CtaButton } from "./CtaButton";

const TRUST_ITEMS = ["申万 31 个一级行业", "5,500+ 只个股", "多源交叉验证"];

/** Hero：H1 + 副标题 + 信任行 + 主/次 CTA，背景为纯 CSS accent 渐变装饰 */
export function Hero() {
  return (
    <section id="hero" className="landing-hero">
      <div className="landing-container landing-hero-inner">
        <h1 className="landing-hero-h1">把一个行业，研究透。</h1>
        <p className="landing-hero-sub">基于官方产能数据与周期模型的 A 股行业投研工作台</p>
        <div className="landing-hero-trust">
          {TRUST_ITEMS.map((t) => (
            <span key={t} className="landing-hero-trust-item">
              <span className="landing-hero-trust-dot" />
              {t}
            </span>
          ))}
        </div>
        <div className="landing-hero-cta-row">
          <CtaButton testId="landing-cta-hero" size="large" />
          <a href="#product">
            <span className="landing-hero-secondary-cta">查看猪周期实况 ↓</span>
          </a>
        </div>
      </div>
    </section>
  );
}
