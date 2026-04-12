import { Typography, Row, Col, Divider } from "antd";
import {
  MarketOverview,
  DistributionChart,
  SectorHeatmap,
  CapitalFlowChart,
  HotSectors,
  IndustryClassification,
} from "@/features/market/components";

export default function MarketPage() {
  return (
    <div>
      <Typography.Title level={4}>全球市场</Typography.Title>
      <MarketOverview />

      <Divider style={{ margin: "16px 0" }} />

      <Typography.Title level={4}>行情中心 · A股</Typography.Title>
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <DistributionChart />
        </Col>
        <Col xs={24} lg={12}>
          <SectorHeatmap />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} lg={12}>
          <CapitalFlowChart />
        </Col>
        <Col xs={24} lg={12}>
          <HotSectors />
        </Col>
      </Row>

      <Divider style={{ margin: "16px 0" }} />

      <IndustryClassification />
    </div>
  );
}
