import { CtaButton } from "./CtaButton";

const TRUST_ITEMS = ["申万 31 个一级行业", "5,500+ 只个股", "多源交叉验证"];

/**
 * CompactHero：免登录行情台首屏压缩版。
 *
 * 行情是主内容，Hero 只保留 H1 + 副标题 + 紧凑信任行 + CTA，
 * 高度目标 ≤ 240px（大图/长信任行已移除，渐变装饰交给 CSS）。
 *
 * 市场状态徽章「A股行情」是**静态字符串**：Task 1.5 只接入了脉搏区（指数条/分布），
 * 未给 Hero 徽章接线；本页范围内不存在可用的交易时段/开闭市时间源，故不做伪驱动。
 * 若后续要真实驱动，需先引入交易日历/时钟源，再改此处。
 */
export function CompactHero() {
  return (
    <section id="hero" className="landing-hero landing-hero--compact">
      <div className="landing-container landing-hero-inner">
        <div className="landing-hero-status">A股行情</div>
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
        </div>
      </div>
    </section>
  );
}
