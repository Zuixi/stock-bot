import { Col, Row, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;

const REGIONS = [
  { key: "asia", label: "亚洲" },
  { key: "americas", label: "美洲" },
] as const;

export function GlobalMarketBoard() {
  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });

  const items = REGIONS.map((r) => ({
    key: r.key,
    label: r.label,
    children: (
      <Row gutter={[12, 12]}>
        {isLoading
          ? Array.from({ length: 6 }, (_, i) => (
              <Col key={i} xs={12} sm={8} xl={4}>
                <Skeleton active paragraph={{ rows: 2 }} />
              </Col>
            ))
          : indices
              .filter((i) => i.region === r.key)
              .map((i) => (
                <Col key={i.tsCode} xs={12} sm={8} xl={4}>
                  <GlobalIndexCardView index={i} />
                </Col>
              ))}
      </Row>
    ),
  }));

  return <Tabs defaultActiveKey="asia" items={items} />;
}
