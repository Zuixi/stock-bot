import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  customTagSuffix,
  fetchSwIndustryTree,
  type SwIndustryLevel1,
} from "@/shared/api/swIndustry";
import { useTheme } from "@/app/theme-context";
import { useMarketPolling } from "../hooks/useMarketPolling";
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
  /** 用户自定义标签**带标注**呈现，绝不加进官方数（见 swIndustry.ts 的 customTagSuffix） */
  const suffix = customTagSuffix(industry);

  return (
    // 整格是跳转入口 → 用真 `<Link>`：可 Tab 聚焦、Enter 触发、可新开标签；
    // 裸 `<div onClick>` 键盘不可达、读屏也不会报成链接（Task 16 / 与「行业分类」卡片同一写法）。
    // 此前这里就是个纯 `<div>`：`.sw-cell:hover` 已经有上浮反馈，于是**看起来能点、点了没反应**。
    // 跳到一级行业下钻页（`/market/industry/:level1Code`，公开路由）。
    //
    // 第二行同时给**官方成分数**与**二级行业个数**：这两个数据点原先是同屏另一张卡
    // 「行业分类（申万）」的独有信息。Phase 1 §W2.2 把那份 31 行业的重复渲染删掉，
    // 只有把这两个有效信息并进瓦片才不是功能回退（口径仍是 SCD2 官方数）。
    <Link
      to={`/market/industry/${industry.code}`}
      className="sw-cell"
      title={`${industry.officialCount ?? industry.stockCount} 只个股 · ${industry.children.length} 个二级行业${
        suffix ? ` · ${suffix}` : ""
      }`}
      style={{
        background: `${colors.accent}${Math.round(alpha * 255)
          .toString(16)
          .padStart(2, "0")}`,
        color: useWhite ? "#ffffff" : "var(--text-primary)",
      }}
    >
      <span className="sw-name">{industry.name}</span>
      {/* 只留两个数据点（官方成分数 · 二级行业数），**不放自定义标注**：瓦片宽度只有
          128–190px，三样信息会把「自定义」挤到第二行，逐格换行、视觉参差（Phase 1 视觉
          复核实测）。自定义标注仍在 `title`（悬停可见）与下钻页（完整口径），信息没丢。 */}
      <span className="sw-count">
        {industry.officialCount ?? industry.stockCount} 只 · {industry.children.length} 个二级
      </span>
    </Link>
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
  const { refetchInterval } = useMarketPolling();
  const { data, isLoading } = useQuery({
    queryKey: ["market", "sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
    refetchInterval,
  });

  const industries = data ?? [];
  // 着色阶梯按**官方成分数**（与显示的数字同一口径；若按 stockCount 着色，深浅会与格子上的数对不上）
  const maxCount = Math.max(0, ...industries.map((i) => i.officialCount));
  const alphaOf = (count: number): number =>
    maxCount > 0 ? ALPHA_MIN + ((ALPHA_MAX - ALPHA_MIN) * count) / maxCount : ALPHA_MIN;

  if (isLoading) return <IndustrySkeletons />;
  if (industries.length === 0) return <div className="sw-fallback">{fallback}</div>;
  return (
    <div className="sw-grid">
      {industries.map((ind) => (
        <IndustryCell key={ind.code} industry={ind} alpha={alphaOf(ind.officialCount)} />
      ))}
    </div>
  );
}
