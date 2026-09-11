import { CompassOutlined, SafetyCertificateOutlined, ThunderboltOutlined } from "@ant-design/icons";

const VALUE_PROPS = [
  {
    icon: <CompassOutlined />,
    title: "周期判定",
    desc: "产能、价格与利润三线索交叉定位，把行业放进繁荣、衰退、萧条、复苏四阶段坐标——而非靠盘感猜顶底。",
  },
  {
    icon: <ThunderboltOutlined />,
    title: "信号与仓位",
    desc: "规则引擎产出买卖信号与仓位建议，每条信号可回溯触发依据；历史信号全部留痕，随时复盘验证。",
  },
  {
    icon: <SafetyCertificateOutlined />,
    title: "数据权威性",
    desc: "官方统计、行业协会、平台行情、期货现货四级来源交叉验证，关键指标带来源徽章，数字可追责。",
  },
];

/** 价值主张三卡：周期判定 / 信号与仓位 / 数据权威性 */
export function ValueProps() {
  return (
    <section className="landing-section" aria-label="核心价值">
      <div className="landing-container">
        <div className="landing-value-grid">
          {VALUE_PROPS.map((p) => (
            <div key={p.title} className="landing-value-card">
              <div className="landing-value-icon">{p.icon}</div>
              <h3 className="landing-value-title">{p.title}</h3>
              <p className="landing-value-desc">{p.desc}</p>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
