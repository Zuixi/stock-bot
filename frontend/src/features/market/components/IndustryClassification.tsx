import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Col, Empty, Row, Skeleton, Space, Tag, Typography } from "antd";
import { useQueries, useQuery } from "@tanstack/react-query";
import { fetchSwIndustryTree, fetchSwLevel1Stocks } from "@/shared/api/swIndustry";

const STALE_TIME = 5 * 60 * 1000;
const SKELETON_COUNT = 12;

export function IndustryClassification() {
  const navigate = useNavigate();
  const { data: tree = [], isLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
    staleTime: STALE_TIME,
  });

  const level1StockQueries = useQueries({
    queries: tree.map((level1) => ({
      queryKey: ["sw-level1-stocks", level1.code],
      queryFn: () => fetchSwLevel1Stocks(level1.code),
      staleTime: STALE_TIME,
    })),
  });

  const level1Cards = useMemo(
    () =>
      tree.map((level1, index) => {
        const stocks = level1StockQueries[index]?.data ?? [];
        const topMoveStocks = [...stocks]
          .sort((a, b) => Math.abs(b.changePercent ?? 0) - Math.abs(a.changePercent ?? 0))
          .slice(0, 3);
        return { level1, stocks, topMoveStocks };
      }),
    [tree, level1StockQueries]
  );

  return (
    <Card
      title="行业分类（申万）"
      size="small"
      extra={<Typography.Text type="secondary">当前页面仅展示一级行业，点击进入二级页面</Typography.Text>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text strong>一级行业</Typography.Text>
        {isLoading ? (
          <Row gutter={[12, 12]}>
            {Array.from({ length: SKELETON_COUNT }).map((_, i) => (
              <Col key={i} xs={12} sm={8} lg={6}>
                <Card size="small">
                  <Skeleton active paragraph={{ rows: 2 }} title={{ width: "50%" }} />
                </Card>
              </Col>
            ))}
          </Row>
        ) : level1Cards.length === 0 ? (
          <Empty description="暂无申万行业数据" />
        ) : (
          <Row gutter={[12, 12]}>
            {level1Cards.map(({ level1, stocks, topMoveStocks }) => {
              return (
                <Col key={level1.code} xs={12} sm={8} lg={6}>
                  <Card
                    hoverable
                    size="small"
                    onClick={() => navigate(`/market/industry/${level1.code}`)}
                  >
                    <Space direction="vertical" size={8} style={{ width: "100%" }}>
                      <Typography.Text strong>{level1.name}</Typography.Text>
                      <Typography.Text type="secondary">{stocks.length} 只个股</Typography.Text>
                      <Space wrap>
                        {topMoveStocks.map((stock) => (
                          <Tag key={stock.symbol} color={(stock.changePercent ?? 0) >= 0 ? "red" : "green"}>
                            {stock.name} {(stock.changePercent ?? 0) > 0 ? "+" : ""}
                            {(stock.changePercent ?? 0).toFixed(2)}%
                          </Tag>
                        ))}
                      </Space>
                    </Space>
                  </Card>
                </Col>
              );
            })}
          </Row>
        )}
      </Space>
    </Card>
  );
}
