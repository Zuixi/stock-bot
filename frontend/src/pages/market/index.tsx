import { useState } from "react";
import { Card, Col, Row, Tabs, Typography } from "antd";
import {
  GlobalMarketBoard,
  CoreIndexCards,
  DistributionChart,
  SectorHeatmap,
  SectorMoneyflowCard,
  MarketMoneyflowCard,
  NorthboundCard,
  HotSectors,
  IndustryClassification,
  MarketDataBoard,
  SwIndustryGrid,
  DataCoverageMatrix,
} from "@/features/market/components";
import "./market.css";

/**
 * 行情中心（Stage C TradingView 化重构，契约 docs/design/landing-market-theme.md §4）：
 * 顶部分类 Tab（指数总览 / A股全景 / 资金流向 / 数据面）+ 卡片阵列，
 * 页脚上方放数据版图精简矩阵（契约 §5）。
 */
export default function MarketPage() {
  const [tab, setTab] = useState("overview");

  const items = [
    {
      key: "overview",
      label: "指数总览",
      children: (
        <Row gutter={[16, 16]}>
          <Col span={24}>
            <GlobalMarketBoard />
          </Col>
          <Col span={24}>
            <CoreIndexCards />
          </Col>
        </Row>
      ),
    },
    {
      key: "panorama",
      label: "A股全景",
      children: (
        <Row gutter={[16, 16]}>
          <Col xs={24} lg={12}>
            <DistributionChart />
          </Col>
          <Col xs={24} lg={12}>
            <SectorHeatmap />
          </Col>
          <Col span={24}>
            <Card title="申万行业网格" size="small">
              <SwIndustryGrid />
            </Card>
          </Col>
          <Col span={24}>
            <HotSectors />
          </Col>
          <Col span={24}>
            <IndustryClassification />
          </Col>
        </Row>
      ),
    },
    {
      key: "moneyflow",
      label: "资金流向",
      children: (
        <Row gutter={[16, 16]}>
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
      ),
    },
    {
      key: "dataface",
      label: "数据面",
      children: (
        <Row gutter={[16, 16]}>
          <Col span={24}>
            <MarketDataBoard />
          </Col>
        </Row>
      ),
    },
  ];

  return (
    <div className="market-page">
      <div className="market-page-head">
        <Typography.Title level={4} style={{ margin: 0 }}>
          行情中心
        </Typography.Title>
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          全球指数 · A股全景 · 资金流向 · 龙虎榜/大宗/解禁/回购
        </Typography.Text>
      </div>

      <Tabs
        data-testid="market-tabs"
        activeKey={tab}
        onChange={setTab}
        items={items}
      />

      <Row gutter={[16, 16]} style={{ marginTop: 24 }}>
        <Col span={24}>
          <Card title="数据版图" size="small">
            <DataCoverageMatrix withStats={false} />
          </Card>
        </Col>
      </Row>
    </div>
  );
}
