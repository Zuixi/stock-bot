import { Card, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { EChart, sparkOption } from "@/shared/ui/EChart";
import { COLORS } from "@/app/theme";
import type { GlobalIndexCard } from "@/shared/api/marketData";

const MARKET_BADGE: Record<string, { label: string; color: string }> = {
  CN: { label: "CN", color: "#ef4444" },
  HK: { label: "HK", color: "#f59e0b" },
  JP: { label: "JP", color: "#1677ff" },
  KR: { label: "KR", color: "#6366f1" },
  US: { label: "US", color: "#0ea5e9" },
};

export function GlobalIndexCardView({ index }: { index: GlobalIndexCard }) {
  const navigate = useNavigate();
  const badge = MARKET_BADGE[index.market] ?? { label: index.market, color: COLORS.flat };
  const up = (index.pctChange ?? 0) > 0;
  const down = (index.pctChange ?? 0) < 0;
  const color = up ? COLORS.up : down ? COLORS.down : COLORS.flat;
  const sparkColor = index.spark.length > 1 ? ((index.spark[index.spark.length - 1] - index.spark[0]) >= 0 ? COLORS.up : COLORS.down) : COLORS.flat;

  return (
    <Card hoverable size="small" onClick={() => navigate(`/index/${index.tsCode}`)} styles={{ body: { padding: "12px 16px" } }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              width: 22, height: 22, borderRadius: "50%", fontSize: 10, fontWeight: 600,
              color: "#fff", backgroundColor: badge.color, flexShrink: 0,
            }}>{badge.label}</span>
            <span style={{ fontSize: 13, fontWeight: 500, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{index.name}</span>
          </div>
          <Typography.Text type="secondary" style={{ fontSize: 10, fontFamily: "monospace" }}>
            {index.market} {index.tsCode.split(".")[0]}
          </Typography.Text>
          <div style={{ fontSize: 20, fontWeight: 600, color, fontVariantNumeric: "tabular-nums", marginTop: 4 }}>
            {index.price == null ? "—" : index.price.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
          </div>
          <div style={{ fontSize: 12, color, fontVariantNumeric: "tabular-nums" }}>
            {index.change == null ? "—" : `${index.change > 0 ? "+" : ""}${index.change.toFixed(2)}`}
            {"  "}
            {index.pctChange == null ? "" : `${index.pctChange > 0 ? "+" : ""}${index.pctChange.toFixed(2)}%`}
          </div>
        </div>
        {index.spark.length > 2 && (
          <div style={{ width: 72, flexShrink: 0 }}>
            <EChart option={sparkOption(index.spark, sparkColor)} height={56} silent />
          </div>
        )}
      </div>
    </Card>
  );
}
