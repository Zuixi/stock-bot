import { useState } from "react";
import { Alert, Button, Card, Col, Row, Segmented } from "antd";
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
  MarketSection,
  SwIndustryGrid,
  SentimentThermometer,
  SentimentPulse,
  SentimentIntradayChart,
  LimitUpLadder,
  SwL3LimitUpBoard,
  YesterdayLimitUp,
} from "@/features/market/components";
import { useMarketPolling } from "@/features/market/hooks/useMarketPolling";
import { NewStockBoard } from "@/features/concept";

/**
 * `/market` 的五个**独立分区**（P1-1 第二轮）。
 *
 * 每个分区自己渲染 {@link MarketSection} 壳体（标题 + 本地维度条 + 内容槽），页面把它们
 * **堆叠**在同一屏里（对标产品的形态：一屏读到多个视角，而不是藏在页级 Tab 后面）。
 * 三条纪律（沿用第一刀，逐区照搬）：
 * 1. **分区壳不接管 `as_of`**：每个面板各自印「数据截至」（各端点的判据日真的不同）；
 * 2. **有真维度的分区才给维度条**：没有可切换维度的分区宁可不给，也不放装饰条；
 * 3. **分区标题说清"这块是干什么的"**，面板自己的卡头标题不重复分区标题。
 */

/**
 * 单卡降级渲染：一张卡渲染异常只坏这一张，不牵连邻卡与整页。
 *
 * 背景：2026-09-21 盘中口径 `l3Code` 为 null 时 `localeCompare` 抛 TypeError，
 * 当时只有全局 ErrorBoundary，结果**整个短线情绪 Tab 被换成「页面渲染出错」**，
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

/** 分区锚点 id（页级 `?tab=` 的滚动目标；见 `pages/market/index.tsx`）。 */
export const sectionAnchor = (key: string) => `market-section-${key}`;

/**
 * 指数总览分区：**全球盘面 → A股基准**两段（先看外面的世界，再看自己的水位）。
 *
 * 维度条是**只读口径行**（不假装可交互）：本分区唯一可切换的维度（亚洲/美洲）本身就是
 * 全球指数卡的卡内 Tab，在分区层再放一个同样的分段控件会是"一个状态两个开关"。
 * 故这里只列"这块有哪两段、各自的刻度是什么"。
 */
export function OverviewSection() {
  return (
    <MarketSection
      title="指数总览（全球盘面 → A股基准）"
      id={sectionAnchor("overview")}
      testId={sectionAnchor("overview")}
      dimensionStrip={
        <>
          <span>① 全球指数 · 亚洲/美洲分组（卡内切换）</span>
          <span>② A股核心指数 · 6 大宽基（纯实时，不印日级口径）</span>
        </>
      }
    >
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <GlobalMarketBoard />
        </Col>
        <Col span={24}>
          <CoreIndexCards />
        </Col>
      </Row>
    </MarketSection>
  );
}

/**
 * A股全景分区：**广度 · 结构 · 资金**三个视角并排（第一行三面板），下面接行业网格与热门板块。
 *
 * 第一行三块各自带自己的「数据截至」：涨跌分布/热力图是"按最近完整日"口径，板块主力资金流
 * 是末完整日快照 —— 实测三块判据日不同（10/13 · 10/13 · 10/15），**分区层不统一口径**。
 * `SectorMoneyflowCard` 用 `variant="compact"`：1440 下它原本 656px（同排另两块 352/382px），
 * 多出的高度全是「板块连续性」区；compact 把它收进默认收起的折叠开关，展开后内容一字不删。
 */
export function PanoramaSection() {
  return (
    <MarketSection
      title="A股全景（广度 · 结构 · 资金）"
      id={sectionAnchor("panorama")}
      testId={sectionAnchor("panorama")}
      dimensionStrip={
        <>
          <span>① 涨跌分布 · 全市场 11 桶</span>
          <span>② 板块热力图 · 申万一级（市值加权）</span>
          <span>③ 板块主力资金流 · 末完整日净流入 Top10</span>
          <span>④ 申万行业网格 · 31 个一级行业（点整格下钻）</span>
          <span>⑤ A股热门板块 · 行业/概念榜（点条目看成分股）</span>
        </>
      }
    >
      <Row gutter={[16, 16]}>
        {/* 三面板并排：宽屏 3-up（xl≥1200 → 8/24）、中屏 2-up（md≥768 → 12/24）、窄屏 1-up。
            每块自带 as_of（DistributionChart / SectorHeatmap / SectorMoneyflowCard 各自
            渲染 `FreshnessNote`），分区层不再重复口径。 */}
        <Col xs={24} md={12} xl={8}>
          <DistributionChart />
        </Col>
        <Col xs={24} md={12} xl={8}>
          <SectorHeatmap />
        </Col>
        <Col xs={24} md={12} xl={8}>
          <SectorMoneyflowCard variant="compact" />
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
    </MarketSection>
  );
}

/**
 * 资金流向分区：**大盘合成 → 北向**。
 *
 * ⚠️ P1-1 第二轮去重：`SectorMoneyflowCard`（板块主力资金流）**不在这里** —— 它随
 * 「广度 · 结构 · 资金」三面板一起落在 {@link PanoramaSection}（同一张卡此前在两个 Tab
 * 各渲染一次，属重复）。本分区只留「大盘资金流」与「北向资金」两块，一屏一主题。
 *
 * 维度条同样是只读口径行：两块都是单一口径的读数（大盘=沪深两市合成 · 北向=盘后成交额），
 * 没有可切换维度。
 */
export function MoneyflowSection() {
  return (
    <MarketSection
      title="资金流向（大盘合成 → 北向）"
      id={sectionAnchor("moneyflow")}
      testId={sectionAnchor("moneyflow")}
      dimensionStrip={
        <>
          <span>① 大盘资金流 · 沪深两市合成 · 近 30 日</span>
          <span>② 北向资金 · 盘后成交额口径（停更与否由卡内自证）</span>
        </>
      }
    >
      <Row gutter={[16, 16]}>
        <Col xs={24} lg={12}>
          <MarketMoneyflowCard />
        </Col>
        <Col xs={24} lg={12}>
          <NorthboundCard />
        </Col>
      </Row>
    </MarketSection>
  );
}

/**
 * 数据面分区：龙虎榜 / 大宗交易 / 解禁 / 回购 / 公告快讯 —— **卡内五视图**。
 *
 * 本分区**刻意不给维度条**：它唯一可切换的维度就是这张卡自己的 Tab 组（`role="tab"` 真控件），
 * 在分区层再放一份同样的分段控件会变成"一个状态两个开关"。分区标题负责说清"这块是干什么的"。
 */
export function DatafaceSection() {
  return (
    <MarketSection
      title="数据面（龙虎榜 · 大宗 · 解禁 · 回购 · 公告）"
      id={sectionAnchor("dataface")}
      testId={sectionAnchor("dataface")}
      extra={<span>五个视图在卡内切换</span>}
    >
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <MarketDataBoard />
        </Col>
      </Row>
    </MarketSection>
  );
}

/**
 * 短线情绪分区：五个端点各自 `useQuery`，单点失败不牵连邻区。
 * 情绪周期日历（market_sentiment_daily）只喂温度计的环比 chip 与趋势线，缺失不阻断主数据。
 * 梯队/申万 L3/昨日涨停/次新股四块各自降级，卡头 `asof` 独立（梯队/次新用 as_of，昨日用 as_of_prev）。
 * 每张数据卡按各自端点的 `degradedReason` 独立门禁，避免降级态仍展示不完整数据。
 *
 * 双口径（Task 13）：顶部「盘中 / 收盘」切换。**默认收盘**（首屏与历史行为一致）；
 * 切盘中后三张涨停卡带 `mode=intraday` 重取、卡头透传后端 `as_of_label`，并在梯队上方
 * 渲染盘中分时曲线。query key 一律把 `mode` 放第三段——同端点两种消费节奏（盘中 30s
 * 刷新、收盘日频）绝不能共用缓存条目（Phase 1 的拆 key 教训，见 `CoreIndexCards`）。
 *
 * ⚠️ P1-1 第二轮：`收盘 / 盘中` 是**本分区唯一的真可切换维度**，故它从分区内容里
 * **上移**到分区维度条（同一份状态、同一个 `data-testid`，只是位置从"内容顶部"变成"维度条"）。
 * 没有把它同时留在两处：一个状态两个开关会让人以为它们各管一段。
 */
export function SentimentSection() {
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
    <MarketSection
      title="短线情绪（温度计 · 连板梯队 · 昨日涨停 · 次新股）"
      id={sectionAnchor("sentiment")}
      testId={sectionAnchor("sentiment")}
      dimensionStrip={
        /* 口径切换：默认收盘。切换只改 query key/参数，不重置任何本地状态 */
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
            {isIntraday
              ? "盘中口径（今日涨停池 · 30 秒刷新 · 不落库）"
              : "收盘口径（本地自算 · 可回放 · 点任一指标看历史）"}
          </span>
        </div>
      }
    >
      <Row gutter={[16, 16]}>
        <Col span={24}>
          <ErrorBoundary fallback={cardFallback("情绪脉搏")}>
            <SentimentPulse />
          </ErrorBoundary>
        </Col>
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
                  // 收盘口径才有可回放的历史（`market_sentiment_daily` 由盘后任务写入）；
                  // 盘中口径不落库，点进去只会看到另一套口径的旧序列，故不可点。
                  metricHref={isIntraday ? undefined : (key) => `/market/sentiment/${key}`}
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
    </MarketSection>
  );
}
