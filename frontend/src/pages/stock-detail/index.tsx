import { useMemo, useRef, useState } from "react";
import { useParams, useNavigate, useSearchParams, Link } from "react-router-dom";
import { Typography, Result, Button, Divider, Row, Col, Spin, Breadcrumb, Tabs } from "antd";
import type { BreadcrumbProps } from "antd";
import { useQuery } from "@tanstack/react-query";
import {
  StockHeader,
  StockTagRow,
  FundamentalCards,
  RelatedEvents,
  FinancialTab,
  ValuationTab,
  CompanyProfile,
  CharacterFacts,
} from "@/features/stock-detail/components";
import { useFinancialSummary } from "@/features/stock-detail/hooks/useFinancialSummary";
import { useStockRangeStats } from "@/features/stock-detail/hooks/useStockRangeStats";
import { useCharacterStats } from "@/features/stock-detail/hooks/useCharacterStats";
import { usePeTtmPercentile } from "@/features/stock-detail/hooks/usePeTtmPercentile";
import { useStockEvents, type EventTab } from "@/features/stock-detail/hooks/useStockEvents";
import { useKlineDates } from "@/features/stock-detail/hooks/useKlineDates";
import {
  eventMarkerPalette,
  indexOfDate,
  toEventMarkers,
} from "@/features/stock-detail/lib/eventMarkers";
import { useTheme } from "@/app/theme-context";
import { BlockBoundary } from "@/shared/ui";
import { KlineChart, type KlineFocusRequest } from "@/shared/ui/kline";
import { fetchKlineBySymbol } from "@/shared/api/quotes";
import { fetchStockEnrichedBySymbol } from "@/shared/api/stocks";
import type { StockRecord } from "@/shared/types";

/**
 * 各块的**实测自然高度**（px），只用作 `BlockBoundary` 错误态的占位高度。
 *
 * 取值来源：P2-1 在 1440×900 下用 Playwright `boundingBox().height` 量的真实渲染高度，
 * 不引入新的间距刻度：
 * - 历史行情 482 = 卡头 40 + ribbon 行 18 + 画布 400（`KlineChart` 里写死的 400）+ 卡内边距 24；
 * - 股性事实行 93 = `padding-top 10` + 1px 上边框 + chip 行 40 + 6 gap + 一句人话 36；
 * - 公司概况 151（四项齐全时）。
 *
 * 为什么占位高度不放在正常渲染态：`CompanyProfile` / `CharacterFacts` 都可以**合法地整块
 * 不渲染**（四项全空 / 样本不足 60 根），给正常态钉最小高度会把"没有记录"画成一块空地。
 */
const KLINE_CARD_MIN_HEIGHT = 482;
const CHARACTER_ROW_MIN_HEIGHT = 93;
const COMPANY_CARD_MIN_HEIGHT = 151;

/** Tab 三态（F13）。非法值一律回落 `overview`，不写回 URL（只读归一化，不替用户改地址栏）。 */
const TAB_KEYS = ["overview", "valuation", "financial"] as const;
type TabKey = (typeof TAB_KEYS)[number];

function normalizeTab(raw: string | null): TabKey {
  return raw === "valuation" || raw === "financial" ? raw : "overview";
}

/**
 * 层级式面包屑：市场 / 申万L1 / L2 / L3 / {股票名}。
 * 链条取自个股自身的申万映射（与入口页无关）；无映射降级为 市场 / 其他 / {股票名}。
 * 层级标签（申万一级等）省略以保持紧凑；末项为当前页不可点击。
 */
function buildBreadcrumbItems(stock: StockRecord): BreadcrumbProps["items"] {
  const chain = stock.swChain ?? [];
  const l1 = chain.find((node) => node.level === 1);
  const l2 = chain.find((node) => node.level === 2);
  const l3 = chain.find((node) => node.level === 3);

  const items: BreadcrumbProps["items"] = [
    { title: <Link to="/market">市场</Link> },
  ];

  if (l1) {
    items.push({ title: <Link to={`/market/industry/${l1.code}`}>{l1.name}</Link> });
    if (l2) {
      // L3 板块挂在二级行业页内（页内标签切换），故 L2/L3 链接指向同一路由
      const l2Route = `/market/industry/${l1.code}/${l2.code}`;
      items.push({ title: <Link to={l2Route}>{l2.name}</Link> });
      if (l3) {
        items.push({ title: <Link to={l2Route}>{l3.name}</Link> });
      }
    }
  } else {
    items.push({ title: <Link to="/market/industry/OTHER">其他</Link> });
  }

  items.push({ title: stock.name });
  return items;
}

export default function StockDetailPage() {
  const { symbol } = useParams<{ symbol: string }>();
  const navigate = useNavigate();

  /**
   * Tab 状态**只**存在 URL 里（F13）：`?tab=overview|valuation|financial`。
   * - 首帧直接读 `searchParams`，所以深链/刷新都直达对应面板（不需要"先渲染概览再跳"）；
   * - 切换用 `replace` —— tab 是同一页面的视图状态，不该在浏览历史里堆栈（否则返回键
   *   会在三个 tab 之间来回，而不是离开详情页）。
   */
  const [searchParams, setSearchParams] = useSearchParams();
  const tab = normalizeTab(searchParams.get("tab"));
  const handleTabChange = (key: string) => {
    setSearchParams({ tab: normalizeTab(key) }, { replace: true });
  };

  const { data: stock, isLoading, isError, error, refetch } = useQuery({
    queryKey: ["stock-detail", symbol],
    queryFn: async () => (symbol ? fetchStockEnrichedBySymbol(symbol) : null),
    enabled: Boolean(symbol),
  });

  /**
   * 财务摘要：概览「成长与盈利」卡与财务 tab 共用**同一个** query key
   * （`["financial-summary", exchange, symbol]`，见 useFinancialSummary）。
   * 因此切到财务 tab 时命中缓存、不二次请求。
   *
   * 必须在下面所有 return 之前调用，保持 hooks 调用顺序稳定。缺失/加载中
   * 时 `data` 为 undefined，卡片显示 `--`，**不阻塞**页面其余部分渲染。
   */
  const summaryQuery = useFinancialSummary(stock?.exchange, stock?.symbol);

  /**
   * 报价条的两段次级读数（F15）。两者都**不新增请求**：
   * - 52 周区间复用 K 线缓存（R7：钉死 qfq，键与 `KlineChart` 逐字相同）；
   * - 估值分位复用估值 tab 的 `["valuation-history", exchange, symbol, "pe_ttm", "3y"]`。
   * 同样在早退之前调用，保持 hooks 顺序。
   */
  const range = useStockRangeStats(symbol);
  const percentile = usePeTtmPercentile(stock?.exchange, stock?.symbol ?? symbol);

  /**
   * 股性事实行（P2-3）。同样在早退之前调用以保持 hooks 顺序；取数复用 K 线在 qfq 下的
   * 缓存键（`useCharacterStats` 内有 R7 注释），**不新增任何请求** ——
   * `e2e/stockDetailCompany.spec.ts` 用「`/quotes/daily` 请求数 == 1」钉住这一点。
   */
  const character = useCharacterStats(symbol);

  /**
   * 事件列表 + 图上事件标记（P1-2）：**同一份查询**同时供给两处。
   *
   * tab 状态由页面持有（`eventKind`），而不是留在 `RelatedEvents` 里：K 线的标记
   * 需要知道"哪一类已加载"，而五个查询沿用原来的 `enabled: activeKind === ...` 懒加载
   * 语义（既有 e2e 钉住"打开详情页时还不该有龙虎榜请求"）。`null` = 非概览 tab，
   * 一个事件请求都不发（与改造前 RelatedEvents 未挂载时一致）。
   */
  const [eventKind, setEventKind] = useState<EventTab>("announcements");
  const overviewActive = tab === "overview";
  const stockEvents = useStockEvents(stock?.symbol, overviewActive ? eventKind : null);

  /**
   * 事件日期 → K 线下标的桥梁。复用的是 **K 线自己的缓存键**（`useKlineDates` 内有 R7 注释），
   * 所以图上的标记、列表行、K 线图永远对齐同一份 bar 序列，且 `/quotes/daily` 仍是 1 次。
   */
  const dates = useKlineDates(stock?.symbol);
  const { colors } = useTheme();
  const markerPalette = useMemo(() => eventMarkerPalette(colors), [colors]);
  const eventMarkers = useMemo(
    () => toEventMarkers(stockEvents.events, dates, markerPalette),
    [stockEvents.events, dates, markerPalette],
  );

  /**
   * 「点列表行 → 图定位」：日期不在已加载 bar 区间（上市前/停牌/周月聚合）时**no-op**，
   * 不崩、不跳到别的日子。`nonce` 让"同一根连点两次"也各算一次新请求。
   */
  const [focusRequest, setFocusRequest] = useState<KlineFocusRequest | undefined>(undefined);
  const focusNonce = useRef(0);
  const handleSelectDate = (date: string) => {
    const index = indexOfDate(dates, date);
    if (index < 0) return;
    focusNonce.current += 1;
    setFocusRequest({ index, nonce: focusNonce.current });
  };

  if (isLoading) {
    return (
      <div style={{ display: "flex", justifyContent: "center", padding: "64px 0" }}>
        <Spin size="large" />
      </div>
    );
  }

  if (isError) {
    const errorMsg = error instanceof Error ? error.message : "获取个股数据失败";
    return (
      <Result
        status="error"
        title="加载失败"
        subTitle={errorMsg}
        extra={[
          <Button key="retry" type="primary" onClick={() => refetch()}>
            重试
          </Button>,
          <Button key="back" onClick={() => navigate("/market")}>
            返回市场
          </Button>,
        ]}
      />
    );
  }

  if (!stock) {
    return (
      <Result
        status="404"
        title="未找到该股票"
        subTitle={`代码 ${symbol ?? ""} 不存在或暂无数据`}
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
      <Breadcrumb style={{ marginBottom: 8 }} items={buildBreadcrumbItems(stock)} />

      {/* 报价条（全宽）：身份 / 现价+涨跌 / 八项事实 / 52 周区间 + 估值分位 + 数据截至 */}
      <StockHeader stock={stock} range={range} percentile={percentile} />

      {/* 分类与标签合并成一行（F14），超出折叠 `+N` */}
      <div style={{ marginTop: 8 }}>
        <StockTagRow exchange={stock.exchange} symbol={stock.symbol} />
      </div>

      <Divider style={{ margin: "16px 0" }} />

      <Tabs
        activeKey={tab}
        onChange={handleTabChange}
        items={[
          {
            key: "overview",
            label: "概览",
            children: (
              <>
                {/* 三段式（D3）：左 16 K 线、右 8 侧栏卡，事件列表全宽。
                    每块各自一层 `BlockBoundary`（不包整页）：一块渲染崩了只坏那一块。 */}
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={16}>
                    <BlockBoundary title="历史行情" minHeight={KLINE_CARD_MIN_HEIGHT}>
                      <KlineChart
                        title="历史行情"
                        queryKey={`stock-kline-${stock.symbol}`}
                        fetcher={(days, adjust) => fetchKlineBySymbol(stock.symbol, days, adjust)}
                        showAdjust
                        eventMarkers={eventMarkers}
                        focusRequest={focusRequest}
                      />
                    </BlockBoundary>
                  </Col>
                  <Col xs={24} lg={8}>
                    {/* 侧栏堆叠顺序（计划 §5 / D3）：估值 → 成长 → 股性 → 公司概况。
                        后两块是 P2 的「事实」区：不套第二层卡片外壳（股性是"事实行"，
                        公司概况有自己的 Card），两者都可能整块不渲染（缺失 ≠ 0）。

                        `FundamentalCards` 内部再给两张卡各包一层边界（只读名录的估值卡
                        与读 `financial-summary` 的成长卡没有共享状态，各自受影响）；
                        成长卡同时接三态（加载 / 无记录 / 失败），失败不再冒充 `--`。 */}
                    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                      <FundamentalCards
                        stock={stock}
                        summary={summaryQuery.data}
                        summaryLoading={summaryQuery.isLoading}
                        summaryError={summaryQuery.isError}
                        onRetrySummary={() => summaryQuery.refetch()}
                      />
                      <BlockBoundary title="股性事实行" minHeight={CHARACTER_ROW_MIN_HEIGHT}>
                        <CharacterFacts stats={character} />
                      </BlockBoundary>
                      <BlockBoundary title="公司概况" minHeight={COMPANY_CARD_MIN_HEIGHT}>
                        <CompanyProfile stock={stock} />
                      </BlockBoundary>
                    </div>
                  </Col>
                </Row>

                <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
                  <Col span={24}>
                    {/* 事件列表的三态（五个 tab 各自 错误 + 重试 / 成功为空 / 加载）
                        已经在 `RelatedEvents` 内做好，这里只补一层渲染错误边界 */}
                    <BlockBoundary title="相关数据">
                      <RelatedEvents
                        events={stockEvents}
                        kind={eventKind}
                        onKindChange={setEventKind}
                        onSelectDate={handleSelectDate}
                      />
                    </BlockBoundary>
                  </Col>
                </Row>

                <Divider style={{ margin: "16px 0" }} />
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  本页数据仅供参考，不构成投资建议。数据来源可能存在延迟，请以交易所实时数据为准。
                </Typography.Text>
              </>
            ),
          },
          {
            key: "valuation",
            label: "估值",
            children: <ValuationTab exchange={stock.exchange} symbol={stock.symbol} />,
          },
          {
            key: "financial",
            label: "财务",
            children: <FinancialTab exchange={stock.exchange} symbol={stock.symbol} />,
          },
        ]}
      />
    </div>
  );
}
