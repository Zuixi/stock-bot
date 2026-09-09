import { Card, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { EChart, sparkOption } from "@/shared/ui/EChart";
import { useTheme } from "@/app/theme-context";
import type { GlobalIndexCard } from "@/shared/api/marketData";

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
  const up = (index.pctChange ?? 0) > 0;
  const down = (index.pctChange ?? 0) < 0;
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
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: "50%", fontSize: 10, fontWeight: 600,
              color: "#fff", backgroundColor: badge.color, flexShrink: 0,
            }}>{badge.label}</span>
            <span style={{
              fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis",
              whiteSpace: "nowrap", color: colors.textPrimary,
            }}>{index.name}</span>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 10, fontFamily: "monospace" }}>
            {index.market} {index.tsCode.split(".")[0]}
          </Typography.Text>
        </div>
        {/* 右列：价格 + 涨跌幅色块右对齐（+/- 号） */}
        <div style={{ textAlign: "right", flexShrink: 0 }}>
          <div style={{
            fontSize: 20, fontWeight: 600, color: colors.textPrimary,
            fontVariantNumeric: "tabular-nums", marginTop: 4,
          }}>
            {index.price == null
              ? "—"
              : index.price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{
            display: "inline-block",
            fontSize: 12, fontWeight: 600,
            color, backgroundColor: `${color}1f`,
            borderRadius: 4, padding: "0 6px", lineHeight: "20px",
            fontVariantNumeric: "tabular-nums", marginTop: 4,
          }}>
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
            <div style={{ width: 88, marginLeft: "auto" }}>
              <EChart option={sparkOption(index.spark, sparkColor)} height={30} silent />
            </div>
          )}
        </div>
      </div>
    </Card>
  );
}
