import { useQuery } from "@tanstack/react-query";
import { Card, Col, Empty, Progress, Row, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { RightOutlined } from "@ant-design/icons";
import { StateWrapper } from "@/shared/ui/StateWrapper";
import { fetchIndustries, type IndustrySummary } from "@/shared/api/industryResearch";

/** 投研工作台入口：已产品化行业列表（由 registry 配置驱动，接入新行业自动出现） */
export default function ResearchPage() {
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["industries"],
    queryFn: fetchIndustries,
  });

  return (
    <div>
      <Typography.Title level={4} style={{ marginBottom: 4 }}>
        投研工作台
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 20 }}>
        已产品化行业的投研看板入口。新行业仅需后端 registry 配置 + 数据源接入即可上线。
      </Typography.Paragraph>

      <StateWrapper loading={isLoading} error={error} onRetry={refetch} empty={!data?.length}>
        <Row gutter={[16, 16]}>
          {(data ?? []).map((item) => (
            <Col key={item.key} xs={24} md={12} lg={8}>
              <IndustryCard industry={item} onOpen={() => navigate(`/research/${item.key}`)} />
            </Col>
          ))}
          {(data ?? []).length === 0 && (
            <Col span={24}>
              <Empty description="暂无已产品化行业" />
            </Col>
          )}
        </Row>
      </StateWrapper>
    </div>
  );
}

function IndustryCard({ industry, onOpen }: { industry: IndustrySummary; onOpen: () => void }) {
  const pct = industry.metricTotal > 0
    ? Math.round((industry.metricWithData / industry.metricTotal) * 100)
    : 0;

  return (
    <Card
      hoverable
      size="small"
      onClick={onOpen}
      styles={{ body: { padding: 20 } }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Typography.Title level={4} style={{ margin: 0 }}>
          {industry.name}
        </Typography.Title>
        {industry.swL3Codes.map((code) => (
          <Tag key={code} style={{ color: "#86909c" }}>
            申万Ⅲ · {code}
          </Tag>
        ))}
        <RightOutlined style={{ marginLeft: "auto", color: "#86909c" }} />
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: 10, marginBottom: 14 }}>
        {industry.description}
      </Typography.Paragraph>
      <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
        <Progress percent={pct} size="small" style={{ flex: 1, marginBottom: 0 }} showInfo={false} />
        <span style={{ fontSize: 12, color: "#86909c", whiteSpace: "nowrap" }}>
          指标接入 {industry.metricWithData}/{industry.metricTotal}
        </span>
      </div>
      {industry.lastPeriod && (
        <div style={{ marginTop: 8, fontSize: 12, color: "#c9cdd4" }}>
          数据截至 {industry.lastPeriod}
        </div>
      )}
    </Card>
  );
}
