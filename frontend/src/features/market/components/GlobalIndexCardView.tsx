import { Card } from "antd";
import { useNavigate } from "react-router-dom";
import { EChart, sparkOption } from "@/shared/ui/EChart";
import { useElementWidth } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { GlobalIndexCard } from "@/shared/api/marketData";
import "./GlobalIndexCardView.css";

/** 市场徽章底色为各地区品牌色（装饰性，不随涨跌主题切换） */
const MARKET_BADGE: Record<string, { label: string; color: string }> = {
  CN: { label: "CN", color: "#ef4444" },
  HK: { label: "HK", color: "#f59e0b" },
  JP: { label: "JP", color: "#1677ff" },
  KR: { label: "KR", color: "#6366f1" },
  US: { label: "US", color: "#0ea5e9" },
};

/**
 * sparkline 画点数：**按容器宽度**决定（Phase 3 §4.1，依 §0.1 B2 修订）。
 *
 * 旧的判据是 `index.spark.length > 2 && width >= 768`——**窄屏直接不画**。B2 裁定
 * "隐藏即违约"，故改为始终渲染、点数随容器退化：
 * 每点约 3px（低于 ~2–3px 相邻点会叠在一起，走势反而看不清），下限 8 点
 * （再少就只剩折线段、没有趋势），上限 60 点。
 *
 * ⚠️ 首帧（`width === 0`，`ResizeObserver` 回调前）**回落成不采样**（画全部点），
 * 不是回落成 8 点：回落成 8 会让首帧先画一条假走势再跳变，而"全画"至少是真实的。
 * 实测该回落只存在一帧（`ResizeObserver` 在首次布局结束即回调）。
 *
 * 数值是**视觉判据**，不是数据契约：点数变化不改变"最后一点 = 最新值"。
 */
function sparkPointsForWidth(width: number): number {
  if (width <= 0) return 0; // 0 = 不采样（调用方按此约定跳过 maxPoints）
  return Math.max(8, Math.min(60, Math.round(width / 3)));
}

/** TV ticker 行规范：名称（左）· 价格右对齐 · 涨跌幅色块右对齐带 +/- 号 */
export function GlobalIndexCardView({ index }: { index: GlobalIndexCard }) {
  const navigate = useNavigate();
  const { colors } = useTheme();
  const badge = MARKET_BADGE[index.market] ?? { label: index.market, color: colors.flat };
  // sparkline 槽位的**实测**宽度（不按视口）：同一视口下不同卡片宽度可以差 2 倍。
  const { ref: sparkRef, width: sparkWidth } = useElementWidth<HTMLDivElement>();
  // 严格比较（`> 0` / `< 0`）而不是 `(pctChange ?? 0) > 0`：后者把 null 折成 0 后再比，
  // 语义上仍是"缺值当成平盘"。严格比较下 null 两侧都为 false → 落到中性色（`colors.flat`），
  // 与"缺值时显示 `—`"同一口径。渲染层本来就不显示 0（见下方 `pctChange == null` 分支）。
  const up = index.pctChange != null && index.pctChange > 0;
  const down = index.pctChange != null && index.pctChange < 0;
  const color = up ? colors.up : down ? colors.down : colors.flat;
  const sparkColor =
    index.spark.length > 1
      ? index.spark[index.spark.length - 1] - index.spark[0] >= 0
        ? colors.up
        : colors.down
      : colors.flat;

  return (
    <Card
      hoverable
      size="small"
      onClick={() => navigate(`/index/${index.tsCode}`)}
      styles={{ body: { padding: "12px 16px" } }}
    >
      {/* 布局全部走 `GlobalIndexCardView.css`（见该文件顶部：为什么必须有一份窄卡形态）。
          这里只保留与主题色/数据相关的行内样式。 */}
      <div className="global-index-card__row">
        <div className="global-index-card__name-col">
          <div className="global-index-card__name-row">
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: "50%", fontSize: 10, fontWeight: 600,
              color: "#fff", backgroundColor: badge.color, flexShrink: 0,
            }}>{badge.label}</span>
            <span className="global-index-card__name" style={{ color: colors.textPrimary }}>
              {index.name}
            </span>
            {/* `CN 000001`：Phase 3 §4.1 把它从独立一行**并入名称行**做 10px 后缀。
                为什么并入而不是留着（实测依据）：独立成行时它是 `.name-col` 里唯一的
                次要文本行，窄卡下名称列只有 ~118px，两行文本都在争同一宽度；
                并入同一行后名称的可用宽度不变（它本来就在第一行），但卡片总高少一行。
                顺序仍是**名称在前**——名称优先级高于代码（同节「禁止把 `上证指数` 截成 `上证…`」）。 */}
            <span className="global-index-card__code">
              {index.market} {index.tsCode.split(".")[0]}
            </span>
          </div>
        </div>
        {/* 右列：价格 + 涨跌幅色块（+/- 号） */}
        <div className="global-index-card__num-col">
          <div className="global-index-card__price num" style={{ color: colors.textPrimary }}>
            {index.price == null
              ? "—"
              : index.price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div className="global-index-card__change num" style={{ color, backgroundColor: `${color}1f` }}>
            {index.pctChange == null
              ? "—"
              : `${index.pctChange > 0 ? "+" : ""}${index.pctChange.toFixed(2)}%`}
            {index.change != null && (
              <span style={{ fontWeight: 400, marginLeft: 4 }}>
                {index.change > 0 ? "+" : ""}{index.change.toFixed(2)}
              </span>
            )}
          </div>
          {/* sparkline：**始终渲染**（B2 —— 不再有"窄屏不画"的分支）。
              点数由 `.global-index-card__spark` 的**实测**宽度决定；首帧宽度未知（0）
              按 0 处理 = 不采样（画全部点，见 `sparkPointsForWidth` 的注释）。 */}
          {index.spark.length > 2 && (
            <div
              className="global-index-card__spark"
              ref={sparkRef}
              // 把"用于画线的点数"与"数据总点数"暴露成可读的 DOM 事实（不是给用户看的）：
              // 否则"点数按容器宽度定"这条 B2 判据只能靠截图目测，无法机械回归。
              // 两者相等 = 未采样（容器够宽或首帧）；小于 = 已按宽度下采样。
              data-spark-points={sparkPointsForWidth(sparkWidth) || index.spark.length}
              data-spark-total={index.spark.length}
            >
              <EChart
                option={sparkOption(
                  index.spark,
                  sparkColor,
                  sparkPointsForWidth(sparkWidth) || undefined
                )}
                height={30}
                silent
              />
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
