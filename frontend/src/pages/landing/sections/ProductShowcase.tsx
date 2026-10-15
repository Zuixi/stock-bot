import { AimOutlined, BulbFilled, FundOutlined, FundProjectionScreenOutlined } from "@ant-design/icons";
import type { ReactNode } from "react";

const MOCK_PHASES = [
  { name: "繁荣", desc: "价格高位 · 产能扩张" },
  { name: "衰退", desc: "盈利收窄 · 库存累积" },
  { name: "萧条", desc: "深度亏损 · 产能出清", active: true },
  { name: "复苏", desc: "价格回升 · 补栏启动" },
];

const MOCK_BADGES = ["已核验", "已溯源", "待复核"];

/**
 * **在途流程**（Phase 4 §5.4）。
 *
 * 原实现给三条特性挂了 `01 / 02 / 03` 编号 —— 但那三条（指标带 / 周期相位条 / 信号面板）
 * 是**并列的能力**，不是序列；用编号把并列读成有序，是在制造一条不存在的信息。
 *
 * 判决书给了二选一（"改为在途流程 **或** 去编号"）。这里选**改为在途流程**，因为本产品
 * 确实有一条真实链路：**原料 → 研判 → 信号 → 仓位**（见 `industry/workbench.tsx` 的
 * 左栏自上而下：指标带 → 猪周期定位 → 信号面板 → 仓位建议，`data-source.md` 的
 * 「周期阶段 / 交易信号 / 仓位建议是规则计算」）。于是第四项不是新编的营销文案，
 * 而是把链路补完整；每一项的状态文字（原料/研判/结论/执行）也都对应真实组件。
 */
const FLOW: Array<{ step: string; title: string; desc: string; icon: ReactNode }> = [
  {
    step: "原料",
    title: "指标带",
    desc: "生猪价格、能繁母猪存栏、猪粮比等关键指标横向排布，溯源徽章一眼可辨。",
    icon: <FundProjectionScreenOutlined />,
  },
  {
    step: "研判",
    title: "周期相位条",
    desc: "繁荣 → 衰退 → 萧条 → 复苏四阶段实时定位，当前相位高亮并给出判定依据。",
    icon: <FundOutlined />,
  },
  {
    step: "结论",
    title: "信号面板",
    desc: "买卖信号与仓位建议联动展示，历史信号可回溯——每个结论都能拆开看触发条件。",
    icon: <BulbFilled />,
  },
  {
    step: "执行",
    title: "仓位建议",
    desc: "信号落到可执行的分批仓位，规则计算而非主观判断，并随信号自动更新。",
    icon: <AimOutlined />,
  },
];

/**
 * 产品展示区（id="product"）：固定深底（light 模式同样保持深色，契约 §3）。
 * 左侧为纯 CSS+DOM 绘制的工作台示意（周期相位条 + 溯源徽章 + 信号卡），不使用截图，
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

          {/* 右：在途流程（原料 → 研判 → 结论 → 执行），编号已删（见上方 FLOW 注释）。
              流程方向由**纵向排列 + 每项的链路位置文字**承担；不在"原料"行尾挂一个
              `↓`（视觉复核实测那是个孤儿字形：它落在行尾，读起来像"原料 ↓"这个整体）。 */}
          <ol className="landing-flow">
            {FLOW.map((f) => (
              <li key={f.step} className="landing-flow__item" data-flow-step={f.step}>
                <span className="landing-flow__marker" aria-hidden="true">
                  {f.icon}
                </span>
                <div className="landing-flow__body">
                  <div className="landing-flow__meta">
                    <span className="landing-flow__step">{f.step}</span>
                  </div>
                  <h3 className="landing-flow__title">{f.title}</h3>
                  <p className="landing-flow__desc">{f.desc}</p>
                </div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </section>
  );
}
