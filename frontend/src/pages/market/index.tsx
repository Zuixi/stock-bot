import { useState } from "react";
import { Card, Col, Row, Tabs, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DegradedNotice, SectionCard } from "@/shared/ui";
import {
  fetchLimitUpLadder,
  fetchSectorLimitUp,
  fetchSentimentCalendar,
  fetchYesterdayLimitUp,
} from "@/shared/api/limitUp";
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
  SentimentThermometer,
  LimitUpLadder,
  SwL3LimitUpBoard,
  YesterdayLimitUp,
} from "@/features/market/components";
import "./market.css";

/**
 * 短线情绪 Tab：四个端点各自 `useQuery`，单点失败不牵连邻区。
 * 情绪周期日历（market_sentiment_daily）只喂温度计的环比 chip 与趋势线，缺失不阻断主数据。
 * 梯队/申万 L3/昨日涨停三块各自降级，卡头 `asof` 独立（梯队用 as_of，昨日用 as_of_prev）。
 * 每张数据卡按各自端点的 `degradedReason` 独立门禁，避免降级态仍展示不完整数据。
 */
function SentimentTab() {
  const ladder = useQuery({
    queryKey: ["limit-up-ladder"],
    queryFn: () => fetchLimitUpLadder(),
    staleTime: 60_000,
  });
  const sectors = useQuery({
    queryKey: ["sector-limit-up"],
    queryFn: () => fetchSectorLimitUp(),
    staleTime: 60_000,
  });
  const yesterday = useQuery({
    queryKey: ["yesterday-limit-up"],
    queryFn: () => fetchYesterdayLimitUp(),
    staleTime: 60_000,
  });
  const calendar = useQuery({
    queryKey: ["sentiment-calendar"],
    queryFn: () => fetchSentimentCalendar(30),
    staleTime: 300_000,
  });
  const degraded = ladder.data?.degradedReason;
  const sectorsDegraded = Boolean(sectors.data?.degradedReason);
  const yesterdayDegraded = Boolean(yesterday.data?.degradedReason);
  return (
    <Row gutter={[16, 16]}>
      <Col span={24}>
        <SectionCard title="情绪温度计" asof={ladder.data?.asOf}>
          {degraded ? (
            <DegradedNotice reason={degraded} />
          ) : (
            <SentimentThermometer
              kpis={ladder.data?.kpis}
              history={calendar.data ?? []}
              asOf={ladder.data?.asOf}
            />
          )}
        </SectionCard>
      </Col>
      <Col span={24}>
        <SectionCard title="连板梯队" asof={ladder.data?.asOf}>
          <LimitUpLadder echelons={ladder.data?.echelons ?? []} degraded={Boolean(degraded)} />
        </SectionCard>
      </Col>
      <Col xs={24} xl={12}>
        <SectionCard title="申万三级最高板" asof={sectors.data?.asOf}>
          <SwL3LimitUpBoard data={sectors.data} degraded={sectorsDegraded} />
        </SectionCard>
      </Col>
      <Col xs={24} xl={12}>
        {/* 卡头「数据截至」用表现日 as_of；涨停日样本口径在组件内标注，
            只标 as_of_prev 会被误读为数据落后（2026-09-15 用户反馈） */}
        <SectionCard title="昨日涨停今日表现" asof={yesterday.data?.asOf}>
          <YesterdayLimitUp data={yesterday.data} degraded={yesterdayDegraded} />
        </SectionCard>
      </Col>
    </Row>
  );
}

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
    {
      key: "sentiment",
      label: "短线情绪",
      children: <SentimentTab />,
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
