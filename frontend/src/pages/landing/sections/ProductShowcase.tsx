import { BulbFilled } from "@ant-design/icons";

const MOCK_PHASES = [
  { name: "繁荣", desc: "价格高位 · 产能扩张" },
  { name: "衰退", desc: "盈利收窄 · 库存累积" },
  { name: "萧条", desc: "深度亏损 · 产能出清", active: true },
  { name: "复苏", desc: "价格回升 · 补栏启动" },
];

const MOCK_BADGES = ["官方", "协会", "平台"];

const FEATURES = [
  {
    title: "指标带",
    desc: "生猪价格、能繁母猪存栏、猪粮比等关键指标横向排布，官方/协会/平台四级来源徽章一眼可辨。",
  },
  {
    title: "周期相位条",
    desc: "繁荣 → 衰退 → 萧条 → 复苏四阶段实时定位，当前相位高亮并给出判定依据。",
  },
  {
    title: "信号面板",
    desc: "买卖信号与仓位建议联动展示，历史信号可回溯——每个结论都能拆开看触发条件。",
  },
];

/**
 * 产品展示区（id="product"）：固定深底（light 模式同样保持深色，契约 §3）。
 * 左侧为纯 CSS+DOM 绘制的工作台示意（周期相位条 + 来源徽章 + 信号卡），不使用截图，
 * 保证明暗两种主题下视觉完全一致。
 */
export function ProductShowcase() {
  return (
    <section id="product" className="landing-showcase">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">工作台长什么样</h2>
          <p className="landing-section-sub">以首个行业实例「猪智投」为例——周期研究方法可复制到更多行业</p>
        </div>
        <div className="landing-showcase-grid">
          {/* 左：CSS 绘制的工作台示意 */}
          <div className="landing-mock" aria-label="工作台界面示意">
            <div className="landing-mock-head">
              <span className="landing-mock-title">生猪养殖 · 猪智投</span>
              <span className="landing-mock-live">● 周期实况</span>
            </div>
            <div className="landing-mock-strip">
              {MOCK_PHASES.map((p) => (
                <div
                  key={p.name}
                  className={p.active ? "landing-mock-phase landing-mock-phase--active" : "landing-mock-phase"}
                >
                  <div className="landing-mock-phase-name">{p.name}</div>
                  <div className="landing-mock-phase-desc">{p.desc}</div>
                </div>
              ))}
            </div>
            <div className="landing-mock-badges">
              {MOCK_BADGES.map((b) => (
              <span key={b} className="landing-mock-badge">
                {b}
              </span>
              ))}
            </div>
            <div className="landing-mock-signal">
              <div className="landing-mock-signal-title">当前信号</div>
              <div className="landing-mock-signal-body">
                <BulbFilled style={{ fontSize: 13, marginRight: 6 }} />
                左侧布局建仓 · 建议仓位 2-3 成
              </div>
              <div className="landing-mock-signal-reason">
                触发依据：能繁存栏连续回落 · 猪粮比处于深度亏损区间 · 产能出清信号出现
              </div>
            </div>
          </div>

          {/* 右：三条特性列表 */}
          <div>
            {FEATURES.map((f, i) => (
              <div key={f.title} className="landing-feature">
                <span className="landing-feature-num">{i + 1}</span>
                <div>
                  <h3 className="landing-feature-title">{f.title}</h3>
                  <p className="landing-feature-desc">{f.desc}</p>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
