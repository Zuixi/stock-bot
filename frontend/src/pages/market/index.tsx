import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Alert, Button, Card, Col, Row, Segmented, Tabs, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DegradedNotice, ErrorBoundary, SectionCard } from "@/shared/ui";
import {
  fetchLimitUpLadder,
  fetchSectorLimitUp,
  fetchSentimentCalendar,
  fetchSentimentIntraday,
  fetchSentimentTrends,
  fetchYesterdayLimitUp,
  type SentimentMode,
} from "@/shared/api/limitUp";
import { fetchNewStocks } from "@/shared/api/concept";
import {
  GlobalMarketBoard,
  CoreIndexCards,
  DistributionChart,
  SectorHeatmap,
  SectorMoneyflowCard,
  MarketMoneyflowCard,
  NorthboundCard,
  HotSectors,
  MarketDataBoard,
  SwIndustryGrid,
  SentimentThermometer,
  SentimentIntradayChart,
  LimitUpLadder,
  SwL3LimitUpBoard,
  YesterdayLimitUp,
} from "@/features/market/components";
import { useMarketPolling } from "@/features/market/hooks/useMarketPolling";
import { NewStockBoard } from "@/features/concept";
import "./market.css";

/**
 * 单卡降级渲染：一张卡渲染异常只坏这一张，不牵连邻卡与整页。
 *
 * 背景：2026-09-21 盘中口径 `l3Code` 为 null 时 `localeCompare` 抛 TypeError，
 * 当时只有全局 ErrorBoundary，结果**整个短线情绪 tab 被换成「页面渲染出错」**，
 * 用户看到的是「页面崩溃、拿不到任何数据」。现在卡级异常 = 卡级告警条 + 重试。
 * 注：这是兵信（防扩散），不是免罪符 —— 渲染层仍然必须对 null 做空合并。
 */
const cardFallback =
  (title: string) =>
  (error: Error, reset: () => void) => (
    <Alert
      type="error"
      showIcon
      message={`${title}渲染失败`}
      description={error.message || "组件渲染发生异常"}
      action={
        <Button size="small" onClick={reset}>
          重试
        </Button>
      }
    />
  );

/**
 * 短线情绪 Tab：五个端点各自 `useQuery`，单点失败不牵连邻区。
 * 情绪周期日历（market_sentiment_daily）只喂温度计的环比 chip 与趋势线，缺失不阻断主数据。
 * 梯队/申万 L3/昨日涨停/次新股四块各自降级，卡头 `asof` 独立（梯队/次新用 as_of，昨日用 as_of_prev）。
 * 每张数据卡按各自端点的 `degradedReason` 独立门禁，避免降级态仍展示不完整数据。
 *
 * 双口径（Task 13）：顶部「盘中 / 收盘」切换。**默认收盘**（首屏与历史行为一致）；
 * 切盘中后三张涨停卡带 `mode=intraday` 重取、卡头透传后端 `as_of_label`，并在梯队上方
 * 渲染盘中分时曲线。query key 一律把 `mode` 放第三段——同端点两种消费节奏（盘中 30s
 * 刷新、收盘日频）绝不能共用缓存条目（Phase 1 的拆 key 教训，见 `CoreIndexCards`）。
 */
function SentimentTab() {
  const [mode, setMode] = useState<SentimentMode>("close");
  const isIntraday = mode === "intraday";
  // 盘中与收盘都走 A 股时段（开市 30s / 休市停）；收盘口径本就是这个节奏，不因新增盘中而提速。
  const { refetchInterval } = useMarketPolling("session");
  const ladder = useQuery({
    queryKey: ["market", "limit-up-ladder", mode],
    queryFn: () => fetchLimitUpLadder(undefined, 10, mode),
    staleTime: isIntraday ? 30_000 : 60_000,
    refetchInterval,
  });
  const sectors = useQuery({
    queryKey: ["market", "sector-limit-up", mode],
    queryFn: () => fetchSectorLimitUp(undefined, undefined, mode),
    staleTime: isIntraday ? 30_000 : 60_000,
    refetchInterval,
  });
  const yesterday = useQuery({
    queryKey: ["market", "yesterday-limit-up", mode],
    queryFn: () => fetchYesterdayLimitUp(undefined, mode),
    staleTime: isIntraday ? 30_000 : 60_000,
    refetchInterval,
  });
  // 分时序列只在盘中口径下取（收盘口径不请求）；date 交给后端按上海时区解析「今天」。
  const intraday = useQuery({
    queryKey: ["market", "sentiment-intraday"],
    queryFn: () => fetchSentimentIntraday(),
    staleTime: 30_000,
    refetchInterval,
    enabled: isIntraday,
  });
  const calendar = useQuery({
    queryKey: ["market", "sentiment-calendar", 30],
    queryFn: () => fetchSentimentCalendar(30),
    staleTime: 300_000,
    refetchInterval,
  });
  // 历史分位 + 长窗口趋势（读时计算，端点侧 Redis 缓存 900s）：只喂温度计的分位 chip 与趋势线，
  // 失败只藏这一块（分量分位在缺证据时必须留白，绝不退回 0/假分位）。
  const trends = useQuery({
    queryKey: ["market", "sentiment-trends", 250],
    queryFn: () => fetchSentimentTrends(250),
    staleTime: 300_000,
    refetchInterval,
  });
  const newStocks = useQuery({
    queryKey: ["new-stocks"],
    queryFn: () => fetchNewStocks(),
    staleTime: 60_000,
  });
  const degraded = ladder.data?.degradedReason;
  const sectorsDegraded = Boolean(sectors.data?.degradedReason);
  const yesterdayDegraded = Boolean(yesterday.data?.degradedReason);
  const newStocksDegraded = Boolean(newStocks.data?.degraded_reason);
  return (
    <div className="sentiment-tab">
      {/* 口径切换：默认收盘。切换只改 query key/参数，不重置任何本地状态 */}
      <div className="sentiment-mode-switch" data-testid="sentiment-mode-switch">
        <Segmented<SentimentMode>
          size="small"
          value={mode}
          onChange={setMode}
          options={[
            { label: "收盘", value: "close" },
            { label: "盘中", value: "intraday" },
          ]}
        />
        <span className="sentiment-mode-switch__hint">
          {isIntraday ? "盘中口径（今日涨停池 · 30 秒刷新）" : "收盘口径（本地自算 · 可回放）"}
        </span>
      </div>
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <SectionCard
            title="情绪温度计"
            asof={ladder.data?.asOf}
            quality={ladder.data?.asOfQuality}
            note={ladder.data?.asOfLabel}
          >
            {degraded ? (
              <DegradedNotice reason={degraded} />
            ) : (
              <ErrorBoundary fallback={cardFallback("情绪温度计")}>
                <SentimentThermometer
                  kpis={ladder.data?.kpis}
                  history={calendar.data ?? []}
                  asOf={ladder.data?.asOf}
                  trends={trends.data}
                />
              </ErrorBoundary>
            )}
          </SectionCard>
        </Col>
        <Col span={24}>
          <SectionCard title="连板梯队" asof={ladder.data?.asOf} quality={ladder.data?.asOfQuality}>
            {/* 盘中口径才画分时曲线：收盘口径没有「分时」这一维度 */}
            {isIntraday ? (
              <ErrorBoundary fallback={cardFallback("盘中分时")}>
                <SentimentIntradayChart points={intraday.data ?? []} />
              </ErrorBoundary>
            ) : null}
            <ErrorBoundary fallback={cardFallback("连板梯队")}>
              <LimitUpLadder echelons={ladder.data?.echelons ?? []} degraded={Boolean(degraded)} />
            </ErrorBoundary>
          </SectionCard>
        </Col>
        <Col xs={24} xl={12}>
          {/* 口径互斥：close = 申万三级（l3Name 有值）；intraday = 东财板块（boardName 有值）。
              卡片自身按 data.source 判定列名，标题由这里按 mode 给出，两侧语义必须一致。 */}
          <SectionCard
            title={isIntraday ? "盘中板块（涨停池口径）" : "申万三级最高板"}
            asof={sectors.data?.asOf}
            quality={sectors.data?.asOfQuality}
          >
            <ErrorBoundary fallback={cardFallback("盘中板块")}>
              <SwL3LimitUpBoard data={sectors.data} degraded={sectorsDegraded} />
            </ErrorBoundary>
          </SectionCard>
        </Col>
        <Col xs={24} xl={12}>
          {/* 卡头「数据截至」用表现日 as_of；涨停日样本口径在组件内标注，
              只标 as_of_prev 会被误读为数据落后（2026-09-15 用户反馈） */}
          <SectionCard
            title="昨日涨停今日表现"
            asof={yesterday.data?.asOf}
            quality={yesterday.data?.asOfQuality}
          >
            <ErrorBoundary fallback={cardFallback("昨日涨停")}>
              <YesterdayLimitUp data={yesterday.data} degraded={yesterdayDegraded} />
            </ErrorBoundary>
          </SectionCard>
        </Col>
        <Col span={24}>
          {/* 次新股情绪（§3 触点 D）：BK0501 成分 + 替代破发口径的声明在组件注脚内 */}
          <SectionCard title="次新股情绪" asof={newStocks.data?.as_of}>
            <ErrorBoundary fallback={cardFallback("次新股情绪")}>
              <NewStockBoard data={newStocks.data} degraded={newStocksDegraded} />
            </ErrorBoundary>
          </SectionCard>
        </Col>
      </Row>
    </div>
  );
}

/**
 * 行情中心（Stage C TradingView 化重构，契约 docs/design/landing-market-theme.md §4）：
 * 顶部分类 Tab（指数总览 / A股全景 / 资金流向 / 数据面 / 短线情绪）+ 卡片阵列。
 */
export default function MarketPage() {
  // Tab 是"视图状态"，必须进 URL：此前是 `useState("overview")`，而 `/market` 与
  // `/company/:symbol` 是兄弟路由，进个股页会把 MarketPage 整棵卸载 —— 按浏览器返回键
  // 只还原 URL，组件重新挂载即落回默认「指数总览」，用户永远回不到离开时的那个 Tab
  // （连带丢失口径/展开态等页内状态）。默认 Tab 不写参数，保住既有 `navigate("/market")`
  // 与 `toHaveURL(/\/market$/)` 的语义；`replace` 避免每点一次 Tab 就往返回栈塞一条。
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = searchParams.get("tab") ?? "overview";
  const setTab = (key: string) =>
    setSearchParams(key === "overview" ? {} : { tab: key }, { replace: true });

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
              {/* Phase 1 §W2.2：这里原先还有一份 `<IndustryClassification />`（31 个行业
                  各一张卡 + 7 项行情聚合条），与上面的网格**同屏渲染同一批 31 个行业**。
                  删的是「重复的行业清单」，不是「行业信息」——每格第二行已补上
                  「N 只 · M 个二级」两个有效数据点。**代价（已知并接受）**：那份清单
                  每卡还带 7 项行情聚合（`IndustryMetricStrip`），删后 `/market` 不再有
                  行业级 7 项行情的 at-a-glance 视图——它仍在行业下钻页（一次点击之外），
                  而 7 项行情条本身在二/三/末级下钻页照常渲染。 */}
              <SwIndustryGrid />
            </Card>
          </Col>
          <Col span={24}>
            <HotSectors />
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
        {/* Phase 1 §W2.3：副标题**由 Tab labels 推导**，不再手写字面量。
            此前是「全球指数 · A股全景 · 资金流向 · 龙虎榜/大宗/解禁/回购」——与 Tab 各说各话
            （第一项 Tab 叫「指数总览」不叫「全球指数」，末项把三个 Tab 塞成一句）。
            同一个列表渲染两处时必须同源，否则加/删 Tab 后副标题必然漂移。 */}
        <Typography.Text type="secondary" style={{ fontSize: 13 }}>
          {items.map((i) => i.label).join(" · ")}
        </Typography.Text>
        {/* Phase 1 §W2.1 的工作台补偿入口：数据版图整块迁出工作台后（它钉在每个 Tab 底部，
            5 个 Tab 各看一遍），这里留一行入口链到独立页 `/market/data-sources`。
            12 = 只读巡检域数（实测 8；取计划 §2.1 的 12 会让这行说谎，故按实测 8 写）。
            ⚠️ **刻意不写供应商名**：计划 §2.1 原稿是「数据覆盖 12 个域 · TuShare/东财 · 查看来源 →」，
            但 `e2e/marketTrust.spec.ts`「公开页不披露数据来源 › /market 整页不得出现供应商名称」
            机械断言 `/market` 正文不得出现 TuShare/东财/巨潮/AKShare —— 那是**既有的、被测试守住的
            产品口径**（数据源构成属竞争资产）。计划那句若照抄就会当场转红；两者冲突时以既有门禁为准，
            故这行去掉供应商名。来源明细仍在独立页（那里也不再按厂商署名，只给表名/域名的实测读数）。 */}
        <Link className="market-page-head__sources" to="/market/data-sources">
          数据覆盖 8 个域 · 查看来源 →
        </Link>
      </div>

      <Tabs
        data-testid="market-tabs"
        activeKey={tab}
        onChange={setTab}
        items={items}
      />
    </div>
  );
}
