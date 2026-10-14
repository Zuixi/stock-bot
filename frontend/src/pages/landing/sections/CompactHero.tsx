import { CtaButton } from "./CtaButton";

const TRUST_ITEMS = ["申万 31 个一级行业", "5,500+ 只个股", "多源交叉验证"];

/**
 * CompactHero：免登录行情台首屏压缩版。
 *
 * 行情是主内容，Hero 只保留 H1 + 副标题 + 紧凑信任行 + CTA，
 * 高度目标 ≤ 240px（大图/长信任行已移除，渐变装饰交给 CSS）。
 *
 * **Phase 4 §5.4 删掉了「A股行情」药丸**：它是**静态字符串**（原注释即写明"未给 Hero 徽章
 * 接线，本页范围内不存在可用的交易时段/开闭市时间源，故不做伪驱动"）。一个恒为「A股行情」
 * 的徽章不携带任何信息、也不随真实状态变化——它不是状态指示器，是装饰。删除而不是"接线"：
 * 本页确实没有可用的时段源，假装驱动是更差的选择。真要做，入口是已存在的
 * `features/market/hooks/marketStatus.ts`（只判工作日与时段、不判法定节假日）。
 */
export function CompactHero() {
  return (
    <section id="hero" className="landing-hero landing-hero--compact">
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
        </div>
      </div>
    </section>
  );
}
