import { Card, Typography } from "antd";
import { ChangeText } from "@/shared/ui";
import { COLORS } from "@/app/theme";
import type { MarketIndex } from "@/shared/types";

interface Props {
  index: MarketIndex;
  onClick?: () => void;
}

export function IndexCard({ index, onClick }: Props) {
  const color = index.changePercent > 0 ? COLORS.up : index.changePercent < 0 ? COLORS.down : COLORS.flat;

  return (
    <Card
      hoverable
      size="small"
      onClick={onClick}
      style={{ minWidth: 160, cursor: onClick ? "pointer" : "default" }}
      styles={{ body: { padding: "12px 16px" } }}
    >
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {index.name}
      </Typography.Text>
      <div style={{ fontSize: 20, fontWeight: 600, color, marginTop: 4, fontVariantNumeric: "tabular-nums" }}>
        {index.value.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
      </div>
      <div style={{ display: "flex", gap: 12, marginTop: 4 }}>
        <ChangeText value={index.change} suffix="" />
        <ChangeText value={index.changePercent} />
      </div>
    </Card>
  );
}
