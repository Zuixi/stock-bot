const PERKS = [
  {
    tier: "公开行情",
    tierSub: "免登录",
    items: "实时指数脉搏 · 涨跌分布 · 申万 31 行业全景 · 个股详情",
    fill: 0.34,
  },
  {
    tier: "受邀账号",
    tierSub: "邀请制",
    items: "+ 自选股跟踪 · 自定义标签 · 多设备同步 · 周期信号提醒 · 行业投研工作台",
    fill: 0.67,
  },
  {
    tier: "持续建设中",
    tierSub: "路线图",
    items: "更多行业投研工作台（周期方法可复制）· 数据域扩展 · 信号回测增强",
    fill: 1,
  },
];

/** 账号能力三档横条（非价格表）：公开行情 / 受邀账号 / 建设中（ADR 0010 邀请制） */
export function AccountPerks() {
  return (
    <section className="landing-section" aria-label="账号能力">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">现在能用什么</h2>
          <p className="landing-section-sub">不搞付费墙——行情数据全公开；账号采用邀请制，由管理员开通，解锁个性化与投研能力</p>
        </div>
        <div className="landing-perks">
          {PERKS.map((p) => (
            <div key={p.tier} className="landing-perk">
              <div>
                <div className="landing-perk-tier">{p.tier}</div>
                <div className="landing-perk-tier-sub">{p.tierSub}</div>
              </div>
              <div>
                <div className="landing-perk-items">{p.items}</div>
                <div className="landing-perk-bar" aria-hidden>
                  <div className="landing-perk-bar-fill" style={{ width: `${p.fill * 100}%` }} />
                </div>
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
