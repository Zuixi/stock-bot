import { Link } from "react-router-dom";
import { SwIndustryGrid } from "@/features/market/components/SwIndustryGrid";

/** 行业网格区块（landing id="industry"）：网格本体已提取至 features/market 共享（Stage C） */
export function IndustryGrid() {
  return (
    <section id="industry" className="landing-section">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">31 个一级行业，一网打尽</h2>
          <p className="landing-section-sub">颜色深浅 = 行业内个股数量，悬浮查看详情</p>
        </div>

        <SwIndustryGrid fallback="行业版图暂不可用——申万 31 个一级行业（农林牧渔、基础化工、机械设备、医药生物、电子……）已就绪，注册后可在工作台查看完整行业树" />

        <p className="landing-industry-footnote">
          首个深度行业：生猪养殖 → <Link to="/research">猪智投</Link>
        </p>
      </div>
    </section>
  );
}
