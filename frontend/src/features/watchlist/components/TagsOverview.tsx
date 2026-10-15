import { useNavigate } from "react-router-dom";
import { Alert, Button, Card, Col, Empty, Row, Space, Spin, Tag, Typography } from "antd";
import { TagsOutlined, LoginOutlined } from "@ant-design/icons";
import { useQuery } from "@tanstack/react-query";
import { fetchAllTags } from "@/shared/api/userTags";
import { useAuth } from "@/features/auth";

/**
 * 标签总览（自定义标签分组 + 每组个股数）。
 *
 * 原 `pages/tags/index.tsx` 的内容**原样提取**为面板，落在 `/me?tab=tags` 下渲染
 * （Ruling 31：`/me` 必须保留已建成的能力，不得用「规划中」占位覆盖）。
 * 卡片点击仍去 `/tags/:tagName`——该路径**是 redirect**（→ `/me?tab=tags&name=:tagName`，
 * 见 `app/router/route-manifest.ts`），个股列表由 `TagsPanel` 渲染 `/me?tab=tags&name=` 这一支。
 */
export function TagsOverview() {
  const navigate = useNavigate();
  const { user, isAuthenticated } = useAuth();

  const { data: tags = [], isLoading } = useQuery({
    queryKey: ["all-tags", user?.id],
    queryFn: fetchAllTags,
    enabled: Boolean(isAuthenticated && user?.id),
    staleTime: 5 * 60 * 1000,
  });

  if (!isAuthenticated) {
    return (
      <Card
        title={
          <Space>
            <TagsOutlined />
            <span>自定义标签</span>
          </Space>
        }
      >
        <Alert
          type="info"
          showIcon
          message="登录后查看个人标签"
          description={
            <Space align="center" style={{ marginTop: 8 }}>
              <span>自定义股票标签属于用户专属数据。请登录后使用标签分类功能。</span>
              <Button type="primary" size="small" icon={<LoginOutlined />} onClick={() => navigate("/login")}>
                立即登录
              </Button>
            </Space>
          }
        />
      </Card>
    );
  }

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
          <Typography.Text type="secondary">点击标签查看对应股票列表</Typography.Text>
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
