import { useQuery } from "@tanstack/react-query";
import { Space } from "antd";
import { IndexCard } from "./IndexCard";
import { fetchMarketIndices } from "@/shared/api/market";

export function MarketOverview() {
  const { data = [] } = useQuery({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
  });

  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
        {data.map((idx) => (
          <IndexCard key={idx.code} index={idx} />
        ))}
      </Space>
    </div>
  );
}
