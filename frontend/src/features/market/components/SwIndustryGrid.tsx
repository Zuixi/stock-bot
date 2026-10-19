import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  customTagSuffix,
  fetchSwIndustryTree,
  type SwIndustryLevel1,
} from "@/shared/api/swIndustry";
import type { PanoramaIndustry } from "@/shared/api/panorama";
import { useMarketPolling } from "../hooks/useMarketPolling";
import "./SwIndustryGrid.css";

/**
 * 数量 → 中性色阶的**五分位阈值**（§4.2 / §3.6）。
 *
 * 为什么不用 accent 叠 alpha（原实现）：`colors.accent` 叠白 alpha 后
 * 0.5→2.10:1、0.85→3.81:1，都不过 AA（需 4.5）；且有 6/31 格命中「白字分支」——
 * 于是**颜色本身在编码数量**，而文字颜色还随数量跳变。现在改成中性灰阶
 * `--mag-0..4`（0 最浅 = 数量最少），文字**一律 `--text-primary`**，无白字分支；
 * 每级对 `--text-primary` 的对比度由 `check-design-tokens.mjs` §8.1-2 逐级算死 ≥4.5。
 *
 * 阈值取**分位**（20/40/60/80 分位）而非等距：行业成分数分布长尾（银行/地产很小、
 * 机械/电子很大），等距会让 90% 的格子挤在最浅那两档，色阶失去分辨力。
 * 用**阈值法**（`count > 阈值`）而非排名法 ⇒ 同一个 count 必得同一档，
 * 颜色是数量的函数，不是渲染顺序的函数。
 */
const QUANTILES = [0.2, 0.4, 0.6, 0.8] as const;

function magThresholds(counts: number[]): number[] {
  const sorted = [...counts].sort((a, b) => a - b);
  if (sorted.length === 0) return [];
  return QUANTILES.map((q) => sorted[Math.min(sorted.length - 1, Math.floor(q * sorted.length))]);
}

/** 0..4：越过几个分位阈值就是第几档（`--mag-0` 最浅 → `--mag-4` 最深）。 */
function magLevelOf(count: number, thresholds: number[]): number {
  let level = 0;
  for (const t of thresholds) if (count > t) level += 1;
  return level;
}

const SKELETON_COUNT = 18;

function IndustryCell({
  industry,
  magLevel,
  pano,
}: {
  industry: SwIndustryLevel1;
  magLevel: number;
  pano?: PanoramaIndustry;
}) {
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
      className={`sw-cell sw-cell--mag-${magLevel}`}
      title={`${industry.officialCount ?? industry.stockCount} 只个股 · ${industry.children.length} 个二级行业${
        pano?.pctChg != null ? ` · 今日 ${pano.pctChg > 0 ? "+" : ""}${pano.pctChg.toFixed(2)}%` : ""
      }${pano?.rankDelta != null ? ` · 排名 ${pano.rankDelta > 0 ? "+" : ""}${pano.rankDelta}` : ""}${
        suffix ? ` · ${suffix}` : ""
      }`}
    >
      <span className="sw-name">{industry.name}</span>
      {pano?.pctChg != null ? (
        <span className={`sw-pct num ${pano.pctChg >= 0 ? "up" : "down"}`}>
          {pano.pctChg > 0 ? "+" : ""}
          {pano.pctChg.toFixed(1)}%
        </span>
      ) : null}
      {/* 只留两个数据点（官方成分数 · 二级行业数），**不放自定义标注**：瓦片宽度只有
          128–190px，三样信息会把「自定义」挤到第二行，逐格换行、视觉参差（Phase 1 视觉
          复核实测）。自定义标注仍在 `title`（悬停可见）与下钻页（完整口径），信息没丢。 */}
      <span className="sw-count num">
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
  /** 全景日表的最新行业涨跌（可选；缺省时瓦片保持纯成分数视图） */
  industries?: PanoramaIndustry[];
}

/**
 * 申万行业网格（TV tile 风）：31 个一级行业按个股数落 `--mag-0..4` 中性色阶的瓦片。
 * Stage C 自 landing/sections/IndustryGrid 提取为共享组件，宣传页与市场页共用。
 * 颜色编码数量的口径见文件头 `QUANTILES` 注释（Phase 2 §4.2/§3.6 起不再用 accent 叠 alpha）。
 */
export function SwIndustryGrid({ fallback = "暂无申万行业数据", industries: panoIndustries }: Props) {
  const { refetchInterval } = useMarketPolling();
  const { data, isLoading } = useQuery({
    queryKey: ["market", "sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
    refetchInterval,
  });

  const industries = data ?? [];
  // 分档按**官方成分数**（与格子上的数字同一口径；若按 stockCount 分档，深浅会与格子上的数对不上）
  const thresholds = magThresholds(industries.map((i) => i.officialCount));
  const panoByCode = new Map((panoIndustries ?? []).map((p) => [p.code, p]));

  if (isLoading) return <IndustrySkeletons />;
  if (industries.length === 0) return <div className="sw-fallback">{fallback}</div>;
  return (
    <div className="sw-grid">
      {industries.map((ind) => (
        <IndustryCell
          key={ind.code}
          pano={panoByCode.get(ind.code)}
          industry={ind}
          magLevel={magLevelOf(ind.officialCount, thresholds)}
        />
      ))}
    </div>
  );
}
