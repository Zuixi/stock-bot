import { Card, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { EChart, sparkOption } from "@/shared/ui/EChart";
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

/** TV ticker 行规范：名称（左）· 价格右对齐 · 涨跌幅色块右对齐带 +/- 号 */
export function GlobalIndexCardView({ index }: { index: GlobalIndexCard }) {
  const navigate = useNavigate();
  const { colors } = useTheme();
  const badge = MARKET_BADGE[index.market] ?? { label: index.market, color: colors.flat };
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
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 10, fontFamily: "monospace" }}>
            {index.market} {index.tsCode.split(".")[0]}
          </Typography.Text>
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
          {index.spark.length > 2 && (
            <div className="global-index-card__spark">
              <EChart option={sparkOption(index.spark, sparkColor)} height={30} silent />
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
