import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Card, Col, Row, Space, Tag, Typography } from "antd";
import { SW_INDUSTRY_TREE, getLevel1Stocks } from "@/shared/mocks/swIndustry";

export function IndustryClassification() {
  const navigate = useNavigate();
  const level1Cards = useMemo(
    () =>
      SW_INDUSTRY_TREE.map((level1) => {
        const stocks = getLevel1Stocks(level1.code);
        const topMoveStocks = [...stocks]
          .sort((a, b) => Math.abs(b.changePercent) - Math.abs(a.changePercent))
          .slice(0, 3);
        return { level1, stocks, topMoveStocks };
      }),
    []
  );

  return (
    <Card
      title="行业分类（申万）"
      size="small"
      extra={<Typography.Text type="secondary">当前页面仅展示一级行业，点击进入二级页面</Typography.Text>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text strong>一级行业</Typography.Text>
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
                        <Tag key={stock.symbol} color={stock.changePercent >= 0 ? "red" : "green"}>
                          {stock.name} {stock.changePercent > 0 ? "+" : ""}
                          {stock.changePercent.toFixed(2)}%
                        </Tag>
                      ))}
                    </Space>
                  </Space>
                </Card>
              </Col>
            );
          })}
        </Row>
      </Space>
    </Card>
  );
}
