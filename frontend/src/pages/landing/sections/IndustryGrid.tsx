import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { fetchSwIndustryTree, type SwIndustryLevel1 } from "@/shared/api/swIndustry";
import { useTheme } from "@/app/theme-context";

/** accent 透明度阶梯区间：个股数最多的一级行业最深，最少的最浅 */
const ALPHA_MIN = 0.08;
const ALPHA_MAX = 0.85;
/** 透明度高于该值时改用白色文字，保证对比度 */
const WHITE_TEXT_THRESHOLD = 0.5;

function IndustryCell({ industry, alpha }: { industry: SwIndustryLevel1; alpha: number }) {
  const { colors } = useTheme();
  const useWhite = alpha > WHITE_TEXT_THRESHOLD;

  return (
    <div
      className="landing-industry-cell"
      title={`${industry.stockCount} 只个股 · ${industry.children.length} 个二级行业`}
      style={{
        background: `${colors.accent}${Math.round(alpha * 255)
          .toString(16)
          .padStart(2, "0")}`,
        color: useWhite ? "#ffffff" : "var(--text-primary)",
      }}
    >
      <span className="landing-industry-name">{industry.name}</span>
      <span className="landing-industry-count">{industry.stockCount} 只</span>
    </div>
  );
}

function IndustrySkeletons() {
  return (
    <div className="landing-industry-grid" aria-hidden>
      {Array.from({ length: 18 }, (_, i) => (
        <div key={i} className="landing-skeleton" style={{ height: 76 }} />
      ))}
    </div>
  );
}

/** 申万行业网格（id="industry"）：31 个一级行业按个股数着色，失败降级为占位文案 */
export function IndustryGrid() {
  const { data, isLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });

  const industries = data ?? [];
  const maxCount = Math.max(0, ...industries.map((i) => i.stockCount));
  const alphaOf = (count: number): number =>
    maxCount > 0 ? ALPHA_MIN + ((ALPHA_MAX - ALPHA_MIN) * count) / maxCount : ALPHA_MIN;

  return (
    <section id="industry" className="landing-section">
      <div className="landing-container">
        <div className="landing-section-head">
          <h2 className="landing-section-title">31 个一级行业，一网打尽</h2>
          <p className="landing-section-sub">颜色深浅 = 行业内个股数量，悬浮查看详情</p>
        </div>

        {isLoading ? (
          <IndustrySkeletons />
        ) : industries.length > 0 ? (
          <div className="landing-industry-grid">
            {industries.map((ind) => (
              <IndustryCell key={ind.code} industry={ind} alpha={alphaOf(ind.stockCount)} />
            ))}
          </div>
        ) : (
          <div className="landing-placeholder">
            行业版图暂不可用——申万 31 个一级行业（农林牧渔、基础化工、机械设备、医药生物、电子……）
            已就绪，注册后可在工作台查看完整行业树
          </div>
        )}

        <p className="landing-industry-footnote">
          首个深度行业：生猪养殖 → <Link to="/research">猪智投</Link>
        </p>
      </div>
    </section>
  );
}
