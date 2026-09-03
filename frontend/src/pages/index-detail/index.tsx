import { useParams, useNavigate, Link } from "react-router-dom";
import { Typography, Result, Button, Divider, Descriptions, Space, Spin, Breadcrumb } from "antd";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftOutlined } from "@ant-design/icons";
import { ChangeText } from "@/shared/ui";
import { fetchMarketIndices } from "@/shared/api/market";
import { IndexKLineChart } from "./IndexKLineChart";

export default function IndexDetailPage() {
  const { tsCode } = useParams<{ tsCode: string }>();
  const navigate = useNavigate();

  const { data: indices = [], isLoading } = useQuery({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    staleTime: 5 * 60 * 1000,
  });

  const index = indices.find((idx) => idx.tsCode === tsCode);

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "64px 0" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (!index) {
    return (
      <Result
        status="404"
        title="未找到该指数"
        subTitle={`指数代码 ${tsCode ?? ""} 不存在或暂无数据`}
        extra={
          <Button type="primary" onClick={() => navigate("/market")}>
            返回市场
          </Button>
        }
      />
    );
  }

  return (
    <div>
      {/* 层级式面包屑：市场 / {指数名}（末项为当前页不可点击） */}
      <Breadcrumb
        style={{ marginBottom: 8 }}
        items={[
          { title: <Link to="/market">市场</Link> },
          { title: index.name },
        ]}
      />
      <Space align="center" size={12} style={{ marginBottom: 16 }}>
        <Button
          type="text"
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate("/market")}
        />
        <div>
          <Space align="center" size={8}>
            <Typography.Title level={3} style={{ margin: 0 }}>
              {index.name}
            </Typography.Title>
            <Typography.Text type="secondary" style={{ fontFamily: "monospace" }}>
              {index.tsCode}
            </Typography.Text>
          </Space>
          <div style={{ marginTop: 4, display: "flex", alignItems: "baseline", gap: 16 }}>
            <span style={{ fontSize: 28, fontWeight: 700 }}>
              {index.value.toLocaleString("zh-CN", { minimumFractionDigits: 2 })}
            </span>
            <ChangeText value={index.change} suffix="" style={{ fontSize: 16 }} />
            <ChangeText value={index.changePercent} style={{ fontSize: 16 }} />
          </div>
        </div>
      </Space>

      <Descriptions size="small" column={3} style={{ marginBottom: 8 }}>
        <Descriptions.Item label="数据截至">{index.asof}</Descriptions.Item>
      </Descriptions>

      <Divider style={{ margin: "16px 0" }} />

      {tsCode && <IndexKLineChart tsCode={tsCode} />}

      <Divider style={{ margin: "16px 0" }} />

      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        本页数据仅供参考，不构成投资建议。数据来源可能存在延迟，请以交易所实时数据为准。
      </Typography.Text>
    </div>
  );
}
