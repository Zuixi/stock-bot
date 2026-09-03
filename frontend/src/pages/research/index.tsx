import { useQuery } from "@tanstack/react-query";
import { Card, Col, Empty, Progress, Row, Tag, Typography } from "antd";
import { useNavigate } from "react-router-dom";
import { RightOutlined } from "@ant-design/icons";
import { StateWrapper } from "@/shared/ui/StateWrapper";
import { fetchIndustries, type IndustrySummary } from "@/shared/api/industryResearch";
import {
  PHASE_COLORS,
  SIGNAL_TEXT_COLORS,
  phaseLabel,
} from "@/features/industry-research/constants";

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
            <Col key={item.key} xs={24} md={12} lg={8} style={{ display: "flex" }}>
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
      style={{ height: "100%", width: "100%" }}
      styles={{ body: { padding: 20 } }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <Typography.Title
          level={4}
          style={{ margin: 0, minWidth: 0 }}
          ellipsis={{ rows: 1 }}
        >
          {industry.name}
        </Typography.Title>
        {industry.swL3Codes.map((code) => (
          <Tag key={code} style={{ color: "#86909c", flexShrink: 0 }}>
            申万Ⅲ · {code}
          </Tag>
        ))}
        <RightOutlined style={{ marginLeft: "auto", color: "#86909c", flexShrink: 0 }} />
      </div>
      <Typography.Text
        type="secondary"
        style={{ display: "block", marginTop: 10, marginBottom: 14 }}
        ellipsis={{ tooltip: true }}
      >
        {industry.description}
      </Typography.Text>
      {(industry.phase || industry.signalType) && (
        <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", marginBottom: 12 }}>
          {industry.phase && (
            <Tag
              style={{
                color: PHASE_COLORS[industry.phase] ?? "#4e5969",
                borderColor: PHASE_COLORS[industry.phase] ?? "#4e5969",
                borderRadius: 14,
                padding: "2px 10px",
                background: "transparent",
              }}
            >
              周期阶段 · {phaseLabel(industry.phase)}
            </Tag>
          )}
          {industry.signalType && (
            <Tag style={{ borderRadius: 14, padding: "2px 10px" }}>
              当前信号 <b style={{ color: SIGNAL_TEXT_COLORS[industry.signalType] ?? "#4e5969" }}>{industry.signalType}</b>
            </Tag>
          )}
          {industry.signalDate && (
            <span style={{ fontSize: 11.5, color: "#c9cdd4" }}>{industry.signalDate}</span>
          )}
        </div>
      )}
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
