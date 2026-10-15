import { Card, Col, Row, Skeleton, Tabs } from "antd";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import { GlobalIndexCardView } from "./GlobalIndexCardView";
import { useMarketPolling } from "../hooks/useMarketPolling";

const STALE_TIME = 60 * 1000;

const REGIONS = [
  { key: "asia", label: "亚洲" },
  { key: "americas", label: "美洲" },
] as const;

export function GlobalMarketBoard() {
  const { refetchInterval } = useMarketPolling("global-index");
  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["market", "global-indices"],
    queryFn: fetchGlobalIndices,
    staleTime: STALE_TIME,
    refetchInterval,
  });

  const items = REGIONS.map((r) => ({
    key: r.key,
    label: r.label,
    children: (
      <Row gutter={[12, 12]}>
        {isLoading
          ? Array.from({ length: 6 }, (_, i) => (
              <Col key={i} xs={24} sm={8} xl={4}>
                <Skeleton active paragraph={{ rows: 2 }} />
              </Col>
            ))
          : indices
              .filter((i) => i.region === r.key)
              .map((i) => (
                // `<768` 一张卡独占整行 —— 与 `CoreIndexCards` 同一理由（§4.1：
                // 名称允许 2 行、名称列下限 104px，152px 的半宽卡装不下）。
                <Col key={i.tsCode} xs={24} sm={8} xl={4}>
                  <GlobalIndexCardView index={i} />
                </Col>
              ))}
      </Row>
    ),
  }));

  return (
    <Card
      title="全球指数"
      size="small"
      // S1.3 全球市场子页入口：本卡片是缩略视图，时段/时区/币种等五要素在子页逐条列出
      extra={<Link to="/market/global">全球市场与跨市场标的 →</Link>}
    >
      <Tabs defaultActiveKey="asia" items={items} />
    </Card>
  );
}
