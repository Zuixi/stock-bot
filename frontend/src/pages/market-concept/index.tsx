import { useMemo, useState, type ReactNode } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import {
  Alert,
  Breadcrumb,
  Card,
  Col,
  Empty,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
} from "antd";
import type { ColumnsType, TableProps } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { ChangeText, DegradedNotice, SectionCard, formatCnDate } from "@/shared/ui";
import { LimitUpLadder } from "@/features/market/components";
import { StockTable } from "@/features/market/components/StockTable";
import { ApiError } from "@/shared/api/client";
import { fetchConceptDetail, fetchConceptStocks, type Echelon } from "@/shared/api/concept";
import type { LadderStock } from "@/shared/api/limitUp";
import type { StockRecord } from "@/shared/types";

/** 概念板块列表页（点击行进入本页 / 404 返回） */
const CONCEPT_LIST_PATH = "/market/hot-sectors/concept";

/**
 * 404 是终态（板块码不存在），重试三次只会让用户多等 ~7s 才看到空态；
 * 5xx / 网络错误仍走默认 3 次重试。
 */
const retryExcept4xx = (failureCount: number, error: unknown) =>
  !(error instanceof ApiError && error.status >= 400 && error.status < 500) && failureCount < 3;

/**
 * `ConceptDetail.echelons`（后端 `EchelonOut`，snake_case 线协议）→ `<LimitUpLadder>` 的
 * `LadderStock`（camelCase 消费端契约）。映射规则与 `shared/api/limitUp.ts:mapStock` 一致：
 * 概念详情是单端点、无 envelope，不在 `/market/limit-up-ladder` 的 mapper 覆盖范围内，故本地适配。
 * `sw_l1_name`/`sw_l3_name` 不在 §2.2 概念梯队的字段集里（梯队卡也不渲染），传 null 不虚构。
 */
function toLadderEchelons(
  echelons: Echelon[]
): Array<{ streak: number; label: string; stocks: LadderStock[] }> {
  return echelons.map((echelon) => ({
    streak: echelon.streak,
    label: echelon.label,
    stocks: echelon.stocks.map((s) => ({
      symbol: s.symbol,
      name: s.name,
      streak: s.streak,
      daysSpan: s.days_span,
      boardsInWindow: s.boards_in_window,
      missingDays: s.missing_days,
      swL1Name: null,
      swL3Name: null,
      sealTime: s.seal_time ?? null,
      sealFund: s.seal_fund ?? null,
      breakCount: s.break_count ?? null,
    })),
  }));
}

type SortState = { sortBy?: keyof StockRecord; sortOrder?: "asc" | "desc" };

/**
 * 成分表是客户端排序（分页仍由 `StockTable` 内部管）：`sortBy`/`sortOrder` 从表头点击来，
 * 行序与表头箭头同源，避免"点了箭头数据不动"的死交互。
 * 缺失值（渲染 `--`）一律排最后：它不参与比较，也不许伪装成 0 抢排位。
 */
function applySort(stocks: StockRecord[], sort: SortState): StockRecord[] {
  if (!sort.sortBy) return stocks;
  const key = sort.sortBy;
  const direction = sort.sortOrder === "asc" ? 1 : -1;
  return [...stocks].sort((a, b) => {
    const av = a[key] as number | undefined;
    const bv = b[key] as number | undefined;
    if (av == null && bv == null) return 0;
    if (av == null) return 1;
    if (bv == null) return -1;
    return av === bv ? 0 : av > bv ? direction : -direction;
  });
}

function KpiTile({ title, children, note }: { title: string; children: ReactNode; note?: string }) {
  return (
    <Card size="small" style={{ height: "100%" }}>
      <Statistic title={title} valueRender={() => children} />
      {note ? (
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          {note}
        </Typography.Text>
      ) : null}
    </Card>
  );
}

/**
 * 概念板块详情（§3 触点 B）。两个互相独立的 `useQuery`：`/concepts/{code}` 先到，
 * 成分列表随后跟上；成分失败只空掉表格，不影响头部/KPI/梯队（反之亦然）。
 * 历史口径声明（「成分截至 X」）是契约要求，不是装饰：明细行只代表当日成分快照。
 */
export default function ConceptBoardPage() {
  const navigate = useNavigate();
  const { boardCode = "" } = useParams();
  // 默认按涨跌幅降序（§3 触点 B）；表头箭头与这个 state 同源
  const [sort, setSort] = useState<SortState>({ sortBy: "changePercent", sortOrder: "desc" });

  const detail = useQuery({
    queryKey: ["concept-detail", boardCode],
    queryFn: () => fetchConceptDetail(boardCode),
    staleTime: 60_000,
    retry: retryExcept4xx,
  });
  const stocks = useQuery({
    queryKey: ["concept-stocks", boardCode],
    queryFn: () => fetchConceptStocks(boardCode),
    enabled: Boolean(detail.data),
    staleTime: 60_000,
  });

  const data = detail.data;
  const board = data?.board;
  const kpis = data?.kpis;
  const ladderEchelons = useMemo(() => toLadderEchelons(data?.echelons ?? []), [data?.echelons]);

  const onTableChange: TableProps<StockRecord>["onChange"] = (_pagination, _filters, sorter) => {
    if (!Array.isArray(sorter) && sorter.field) {
      setSort({
        sortBy: sorter.field as keyof StockRecord,
        sortOrder: sorter.order === "ascend" ? "asc" : "desc",
      });
    }
  };

  const displayStocks = useMemo(() => applySort(stocks.data ?? [], sort), [stocks.data, sort]);

  // 连板列：`echelons` 拍平成 {symbol: streak}；不在梯队 → `--`（缺失 ≠ 0）。
  // 字段不在 `StockRecord` 上，故不挂 sorter —— 排不了序的箭头就是假交互。
  const extraColumns: ColumnsType<StockRecord> = useMemo(() => {
    const streakBySymbol = new Map<string, number>();
    for (const echelon of data?.echelons ?? []) {
      for (const stock of echelon.stocks) {
        streakBySymbol.set(stock.symbol, stock.streak);
      }
    }
    return [
      {
        title: "连板",
        key: "streak",
        width: 72,
        align: "right" as const,
        render: (_: unknown, record: StockRecord) => {
          const streak = streakBySymbol.get(record.symbol);
          return streak == null ? "--" : `${streak}板`;
        },
      },
    ];
  }, [data?.echelons]);

  // KPI 全部来自同一份响应，不新造口径：占比的分母 = 有行情成分数（up+flat+down）。
  const pricedCount = board ? board.up_count + board.flat_count + board.down_count : 0;
  const upRatio = board && pricedCount > 0 ? (board.up_count / pricedCount) * 100 : null;
  const inflowYi = board?.main_net_inflow != null ? board.main_net_inflow / 1e8 : null;
  const notFound = detail.error instanceof ApiError && detail.error.status === 404;

  return (
    <div data-testid="concept-board">
      <Space direction="vertical" size={16} style={{ width: "100%" }}>
        <Breadcrumb
          items={[
            { title: <a onClick={() => navigate("/market")}>市场</a> },
            { title: <a onClick={() => navigate(CONCEPT_LIST_PATH)}>A股热门板块</a> },
            { title: board?.board_name ?? boardCode },
          ]}
        />

        {detail.isLoading ? (
          <Card size="small">
            <div style={{ display: "flex", justifyContent: "center", padding: 24 }}>
              <Spin />
            </div>
          </Card>
        ) : null}

        {notFound ? (
          <Card size="small">
            <Empty description="未找到该概念板块">
              <Link to={CONCEPT_LIST_PATH}>返回概念板块列表</Link>
            </Empty>
          </Card>
        ) : null}

        {detail.isError && !notFound ? (
          <Alert
            type="error"
            showIcon
            message="概念板块数据加载失败"
            description={detail.error instanceof Error ? detail.error.message : undefined}
          />
        ) : null}

        {board && data ? (
          <SectionCard title={board.board_name} asof={data.as_of}>
            <Space direction="vertical" size={8} style={{ width: "100%" }}>
              {data.degraded_reason ? <DegradedNotice reason={data.degraded_reason} /> : null}
              {data.unresolved_count > 0 ? (
                <Alert
                  type="warning"
                  showIcon
                  data-testid="concept-unresolved"
                  message={`另有 ${data.unresolved_count} 只成分股未收录（名录待刷新），未参与涨跌统计`}
                />
              ) : null}
              <Space size={12} wrap align="center">
                <Tag>{board.board_code}</Tag>
                <ChangeText value={board.avg_pct} style={{ fontSize: 16, fontWeight: 600 }} />
                <Typography.Text type="secondary">
                  上涨 {board.up_count} · 平盘 {board.flat_count} · 下跌 {board.down_count} · 成分{" "}
                  {board.member_count}
                </Typography.Text>
              </Space>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                成分截至 {data.membership_as_of ? formatCnDate(data.membership_as_of) : "--"}（东财）·
                涨跌按本地聚合（n={pricedCount} 有行情家数）
              </Typography.Text>
            </Space>
          </SectionCard>
        ) : null}

        {board ? (
          <Row gutter={[16, 16]}>
            <Col xs={12} md={6}>
              <KpiTile title="板内涨停" note="口径同连板梯队">
                {kpis ? (
                  <span>
                    {kpis.zt_count}
                    <span style={{ fontSize: 14, marginLeft: 4 }}>家</span>
                  </span>
                ) : (
                  <span>--</span>
                )}
              </KpiTile>
            </Col>
            <Col xs={12} md={6}>
              <KpiTile title="最高板" note={`龙头 ${kpis?.leader_name ?? "--"}`}>
                {/* 后端把 max_streak=0 定义为「无涨停」（是数据，不是缺失，呼应邻格 0 家）；
                    只有整个 kpis 缺失才渲染 `--`（缺失 ≠ 0）。 */}
                <span>{kpis ? `${kpis.max_streak}连板` : "--"}</span>
              </KpiTile>
            </Col>
            <Col xs={12} md={6}>
              <KpiTile title="主力净流入" note="东财快照；缺行显示 --">
                <span>{inflowYi == null ? "--" : `${inflowYi.toFixed(2)} 亿`}</span>
              </KpiTile>
            </Col>
            <Col xs={12} md={6}>
              <KpiTile title="今日上涨占比" note={`n=${pricedCount} 有行情家数`}>
                <span>{upRatio == null ? "--" : `${upRatio.toFixed(1)}%`}</span>
              </KpiTile>
            </Col>
          </Row>
        ) : null}

        {data ? (
          <SectionCard title="板内连板梯队" asof={data.as_of}>
            {/* 空态文案按 §3 触点 B：「今日板内无涨停」（非降级才可能是真空态；降级仍走组件的占位） */}
            {ladderEchelons.length === 0 && !data.degraded_reason ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="今日板内无涨停" />
            ) : (
              <LimitUpLadder echelons={ladderEchelons} degraded={Boolean(data.degraded_reason)} />
            )}
          </SectionCard>
        ) : null}

        {board && data ? (
          <SectionCard title="成分股" asof={data.as_of}>
            {stocks.isError ? (
              <Alert
                type="error"
                showIcon
                message="成分列表加载失败"
                description={stocks.error instanceof Error ? stocks.error.message : undefined}
              />
            ) : null}
            <StockTable
              data={displayStocks}
              loading={stocks.isLoading}
              onChange={onTableChange}
              sortBy={sort.sortBy}
              sortOrder={sort.sortOrder === "asc" ? "ascend" : "descend"}
              extraColumns={extraColumns}
            />
          </SectionCard>
        ) : null}
      </Space>
    </div>
  );
}
