import { useQuery } from "@tanstack/react-query";
import { Breadcrumb, Card, Col, Empty, Row, Tabs, Tag, Typography } from "antd";
import { useParams } from "react-router-dom";
import { StateWrapper } from "@/shared/ui/StateWrapper";
import { COLORS } from "@/app/theme";
import { fetchIndustryDashboard, type Dashboard } from "@/shared/api/industryResearch";
import { IndicatorStrip } from "@/features/industry-research/components/IndicatorStrip";
import { IndicatorGrid } from "@/features/industry-research/components/IndicatorGrid";
import { CyclePhaseStrip } from "@/features/industry-research/components/CyclePhaseStrip";
import { SignalPanel } from "@/features/industry-research/components/SignalPanel";
import { PositionAdviceBar } from "@/features/industry-research/components/PositionAdviceBar";
import { PriceCostChart } from "@/features/industry-research/components/PriceCostChart";
import { SowTrendChart } from "@/features/industry-research/components/SowTrendChart";
import { SourceBadge } from "@/features/industry-research/components/SourceBadge";
import { CompanyComparisonTable } from "@/features/industry-research/components/CompanyComparisonTable";

const PHASE_COLORS: Record<string, string> = {
  prosperity: "#cf1322",
  recession: "#d48806",
  depression: "#4e5969",
  recovery: "#1677ff",
};

const SIGNAL_TEXT_COLORS: Record<string, string> = {
  买入: "#cf1322",
  卖出: "#389e0d",
  关注: "#d48806",
  空仓: "#8c8c8c",
};

/** 行业投研工作台 — 投资看板（P1-P4）；知识库/调研追踪/交易管理为后续阶段占位 */
export default function ResearchWorkbenchPage() {
  const { industryKey = "pig" } = useParams();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["industry-dashboard", industryKey],
    queryFn: () => fetchIndustryDashboard(industryKey),
    staleTime: 60 * 1000,
  });

  return (
    <div style={{ maxWidth: 1720, margin: "0 auto" }}>
      <Breadcrumb style={{ marginBottom: 10, fontSize: 13 }} items={[{ title: "投研" }, { title: "工作台" }]} />
      <StateWrapper loading={isLoading} error={error} onRetry={refetch} empty={!data}>
        {data && <Workbench dashboard={data} />}
      </StateWrapper>
    </div>
  );
}

function Workbench({ dashboard }: { dashboard: Dashboard }) {
  const { industry, cycle, signal } = dashboard;
  const phaseColor = PHASE_COLORS[cycle.phase] ?? COLORS.flat;
  const signalColor = SIGNAL_TEXT_COLORS[signal.signalType] ?? COLORS.flat;
  const phaseLabel =
    dashboard.cycle.phases.find((p) => p.key === cycle.phase)?.label ?? cycle.phase;

  const tabItems = [
    {
      key: "dashboard",
      label: "投资看板",
      children: (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <IndicatorStrip metrics={dashboard.strip} />

          <Row gutter={[16, 16]}>
            <Col xs={24} lg={14}>
              <Card size="small" title="猪周期阶段定位" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>判定因子：猪粮比 · 能繁产能 · 行业盈亏</span>}>
                <CyclePhaseStrip cycle={cycle} />
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card size="small" title="交易信号面板" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>规则引擎 · 信号历史可回测</span>}>
                <SignalPanel current={signal} history={dashboard.signalHistory} />
              </Card>
            </Col>
            <Col xs={24} lg={14}>
              <Card size="small" title="生猪价格 vs 行业成本" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>月度 · 元/kg</span>}>
                <PriceCostChart trend={dashboard.trends.price_vs_cost} />
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card size="small" title="仓位管理建议" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>随信号联动 · 非投资建议</span>}>
                <PositionAdviceBar positions={signal.positions} />
              </Card>
            </Col>
            <Col xs={24} lg={14}>
              <Card size="small" title="能繁母猪存栏趋势" extra={<SourceBadge tier="official" />}>
                <SowTrendChart trend={dashboard.trends.sow_inventory} />
              </Card>
            </Col>
            <Col xs={24} lg={10}>
              <Card size="small" title="核心指标速览" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>{dashboard.quickView.length} 项</span>}>
                <IndicatorGrid metrics={dashboard.quickView} />
              </Card>
            </Col>
          </Row>
        </div>
      ),
    },
    {
      key: "knowledge",
      label: "行业知识库",
      children: <Empty description="行业思维导图 / 利益相关机构图谱 — P6 阶段上线" />,
    },
    {
      key: "tracking",
      label: "行情调研追踪",
      // 标的分析（P5）：成分股对比表。antd Tabs 惰性挂载 pane，本组件的 useQuery
      // 只在 Tab 首次激活时发起，看板首屏不受影响；ETF/转债表（P5 行情）后续并列于此。
      children: (
        <Card size="small" title="标的分析 · 成分股对比" extra={<span style={{ fontSize: 11.5, color: "#86909c" }}>点击行进入个股详情 · 公司指标列由 registry 下发</span>}>
          <CompanyComparisonTable industryKey={industry.key} />
        </Card>
      ),
    },
    {
      key: "trading",
      label: "交易管理",
      children: <Empty description="交易决策 / 策略管理 — 规划中" />,
    },
  ];

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
        <Typography.Title level={3} style={{ margin: 0 }}>
          {industry.name}
          <span style={{ fontSize: 15, fontWeight: 500, color: "#4e5969", marginLeft: 10 }}>
            投研工作台
          </span>
        </Typography.Title>
        <div style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <Tag color="#12213c" style={{ color: "#fff", border: "none", borderRadius: 14, padding: "2px 12px" }}>
            周期阶段 {phaseLabel}
          </Tag>
          <Tag color="gold" style={{ borderRadius: 14, padding: "2px 12px" }}>
            当前信号 <b style={{ color: signalColor }}>{signal.signalType}</b>
          </Tag>
          <Tag style={{ borderRadius: 14, padding: "2px 12px", color: "#86909c" }}>
            数据截至 {dashboard.asOf}
          </Tag>
          {dashboard.dataSource === "mock" && (
            <Tag style={{ borderRadius: 14, padding: "2px 12px", color: "#86909c", borderStyle: "dashed" }}>
              演示数据源：mock
            </Tag>
          )}
        </div>
      </div>

      <Tabs
        style={{ marginTop: 12 }}
        defaultActiveKey="dashboard"
        items={tabItems}
        tabBarStyle={{ marginBottom: 4 }}
      />
    </div>
  );
}
