import { useNavigate } from "react-router-dom";
import { Card, Col, Empty, Row, Skeleton, Space, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchSwIndustryTree } from "@/shared/api/swIndustry";

const STALE_TIME = 5 * 60 * 1000;
const SKELETON_COUNT = 12;

export function IndustryClassification() {
  const navigate = useNavigate();
  const { data: tree = [], isLoading } = useQuery({
    queryKey: ["sw-industry-tree"],
    queryFn: fetchSwIndustryTree,
    staleTime: STALE_TIME,
  });

  return (
    <Card
      title="行业分类（申万）"
      size="small"
      extra={<Typography.Text type="secondary">当前页面仅展示一级行业，点击进入二级页面</Typography.Text>}
    >
      <Space direction="vertical" size={12} style={{ width: "100%" }}>
        <Typography.Text strong>一级行业（{tree.length}）</Typography.Text>
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
        ) : tree.length === 0 ? (
          <Empty description="暂无申万行业数据" />
        ) : (
          <Row gutter={[12, 12]}>
            {tree.map((level1) => (
              <Col key={level1.code} xs={12} sm={8} lg={6}>
                <Card
                  hoverable
                  size="small"
                  onClick={() => navigate(`/market/industry/${level1.code}`)}
                >
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Typography.Text strong>{level1.name}</Typography.Text>
                    <Space size={8}>
                      <Tag color="blue">{level1.stockCount} 只个股</Tag>
                      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                        {level1.children.length} 个二级行业
                      </Typography.Text>
                    </Space>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Space>
    </Card>
  );
}
