import { Space } from "antd";
import { IndexCard } from "./IndexCard";
import { MOCK_INDICES } from "@/shared/mocks/market";

export function MarketOverview() {
  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
        {MOCK_INDICES.map((idx) => (
          <IndexCard key={idx.code} index={idx} />
        ))}
      </Space>
    </div>
  );
}
