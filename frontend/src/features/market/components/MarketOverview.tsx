import { useQuery } from "@tanstack/react-query";
import { Card, Skeleton, Space } from "antd";
import { useNavigate } from "react-router-dom";
import { IndexCard } from "./IndexCard";
import { fetchMarketIndices } from "@/shared/api/market";

const STALE_TIME = 5 * 60 * 1000;
const SKELETON_COUNT = 6;

export function MarketOverview() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    staleTime: STALE_TIME,
  });

  if (isLoading) {
    return (
      <div style={{ overflowX: "auto", paddingBottom: 4 }}>
        <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
          {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
            <Card key={i} size="small" style={{ minWidth: 160 }} styles={{ body: { padding: "12px 16px" } }}>
              <Skeleton active paragraph={{ rows: 2, width: ["60%", "80%"] }} title={{ width: "40%" }} />
            </Card>
          ))}
        </Space>
      </div>
    );
  }

  return (
    <div style={{ overflowX: "auto", paddingBottom: 4 }}>
      <Space size={12} style={{ display: "flex", flexWrap: "nowrap" }}>
        {data.map((idx) => (
          <IndexCard
            key={idx.code}
            index={idx}
            onClick={() => navigate(`/index/${idx.tsCode}`)}
          />
        ))}
      </Space>
    </div>
  );
}
