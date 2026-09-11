import { CtaButton } from "./CtaButton";

/** 底部 CTA：同一套登录态按钮逻辑（已登录→进入工作台，未登录→免费开始） */
export function BottomCTA() {
  return (
    <section className="landing-bottom-cta">
      <div className="landing-container">
        <h2 className="landing-bottom-cta-title">现在注册，30 秒开始你的第一次行业研究</h2>
        <CtaButton testId="landing-cta-bottom" size="large" />
      </div>
    </section>
  );
}
