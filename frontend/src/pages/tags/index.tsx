import { useNavigate } from "react-router-dom";
import { Card, Col, Empty, Row, Space, Spin, Tag, Typography } from "antd";
import { TagsOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { fetchAllTags } from "@/shared/api/userTags";

export default function TagsPage() {
  const navigate = useNavigate();

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ["all-tags"],
    queryFn: fetchAllTags,
    staleTime: 5 * 60 * 1000,
  });

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "64px 0" }}>
        <Spin size="large" />
      </div>
    );
  }

  return (
    <Space direction="vertical" size={16} style={{ width: "100%" }}>
      <Card
        title={
          <Space>
            <TagsOutlined />
            <span>自定义标签</span>
          </Space>
        }
        size="small"
        extra={
          <Typography.Text type="secondary">
            点击标签查看对应股票列表
          </Typography.Text>
        }
      >
        {tags.length === 0 ? (
          <Empty description="暂无自定义标签，可在股票详情页添加" />
        ) : (
          <Row gutter={[12, 12]}>
            {tags.map((tag) => (
              <Col key={tag.tag_name} xs={24} sm={12} md={8} lg={6}>
                <Card
                  hoverable
                  size="small"
                  onClick={() => navigate(`/tags/${encodeURIComponent(tag.tag_name)}`)}
                >
                  <Space direction="vertical" size={4} style={{ width: "100%" }}>
                    <Typography.Text strong style={{ fontSize: 16 }}>
                      {tag.tag_name}
                    </Typography.Text>
                    <Tag color="green">{tag.stock_count} 只个股</Tag>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>
        )}
      </Card>
    </Space>
  );
}
