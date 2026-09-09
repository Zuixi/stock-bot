import { useQuery } from "@tanstack/react-query";
import { fetchSwIndustryTree, type SwIndustryLevel1 } from "@/shared/api/swIndustry";
import { useTheme } from "@/app/theme-context";
import "./SwIndustryGrid.css";

/** accent 透明度阶梯区间：个股数最多的一级行业最深，最少的最浅 */
const ALPHA_MIN = 0.08;
const ALPHA_MAX = 0.85;
/** 透明度高于该值时改用白色文字，保证对比度 */
const WHITE_TEXT_THRESHOLD = 0.5;

const SKELETON_COUNT = 18;

function IndustryCell({ industry, alpha }: { industry: SwIndustryLevel1; alpha: number }) {
  const { colors } = useTheme();
  const useWhite = alpha > WHITE_TEXT_THRESHOLD;

  return (
    <div
      className="sw-cell"
      title={`${industry.stockCount} 只个股 · ${industry.children.length} 个二级行业`}
      style={{
        background: `${colors.accent}${Math.round(alpha * 255)
          .toString(16)
          .padStart(2, "0")}`,
        color: useWhite ? "#ffffff" : "var(--text-primary)",
      }}
    >
      <span className="sw-name">{industry.name}</span>
      <span className="sw-count">{industry.stockCount} 只</span>
    </div>
  );
}

function IndustrySkeletons() {
  return (
    <div className="sw-grid" aria-hidden>
      {Array.from({ length: SKELETON_COUNT }, (_, i) => (
        <div key={i} className="sw-skeleton" style={{ height: 76 }} />
      ))}
    </div>
  );
}

interface Props {
  /** 数据不可用时的降级文案：landing 带营销口吻，工作台传中性提示 */
  fallback?: string;
}

/**
 * 申万行业网格（TV tile 风）：31 个一级行业按个股数着色的 accent 阶梯瓦片。
 * Stage C 自 landing/sections/IndustryGrid 提取为共享组件，宣传页与市场页共用。
 */
export function SwIndustryGrid({ fallback = "暂无申万行业数据" }: Props) {
  const { data, isLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
  });

  const industries = data ?? [];
  const maxCount = Math.max(0, ...industries.map((i) => i.stockCount));
  const alphaOf = (count: number): number =>
    maxCount > 0 ? ALPHA_MIN + ((ALPHA_MAX - ALPHA_MIN) * count) / maxCount : ALPHA_MIN;

  if (isLoading) return <IndustrySkeletons />;
  if (industries.length === 0) return <div className="sw-fallback">{fallback}</div>;
  return (
    <div className="sw-grid">
      {industries.map((ind) => (
        <IndustryCell key={ind.code} industry={ind} alpha={alphaOf(ind.stockCount)} />
      ))}
    </div>
  );
}
