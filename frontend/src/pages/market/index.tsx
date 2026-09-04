import { Typography, Row, Col, Divider } from "antd";
import {
  GlobalMarketBoard,
  DistributionChart,
  SectorHeatmap,
  SectorMoneyflowCard,
  MarketMoneyflowCard,
  NorthboundCard,
  HotSectors,
  IndustryClassification,
  MarketDataBoard,
} from "@/features/market/components";

export default function MarketPage() {
  return (
    <div>
      <Typography.Title level={4}>全球市场</Typography.Title>
      <GlobalMarketBoard />

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
        <Col xs={24} lg={12} xl={8}>
          <SectorMoneyflowCard />
        </Col>
        <Col xs={24} lg={12} xl={8}>
          <MarketMoneyflowCard />
        </Col>
        <Col xs={24} lg={12} xl={8}>
          <NorthboundCard />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <HotSectors />
        </Col>
      </Row>

      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col span={24}>
          <MarketDataBoard />
        </Col>
      </Row>

      <Divider style={{ margin: "16px 0" }} />

      <IndustryClassification />
    </div>
  );
}
