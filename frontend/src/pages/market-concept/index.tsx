import { useMemo, type ReactNode } from "react";
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
import { useQuery } from "@tanstack/react-query";
import { ChangeText, DegradedNotice, SectionCard, formatCnDate } from "@/shared/ui";
import { LimitUpLadder } from "@/features/market/components";
import { StockTable } from "@/features/market/components/StockTable";
import { ApiError } from "@/shared/api/client";
import { fetchConceptDetail, fetchConceptStocks, type Echelon } from "@/shared/api/concept";
import type { LadderStock } from "@/shared/api/limitUp";

/** 概念板块列表页（点击行进入本页 / 404 返回） */
const CONCEPT_LIST_PATH = "/market/hot-sectors/concept";

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

  const detail = useQuery({
    queryKey: ["concept-detail", boardCode],
    queryFn: () => fetchConceptDetail(boardCode),
    staleTime: 60_000,
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
                <span>
                  {kpis?.zt_count ?? 0}
                  <span style={{ fontSize: 14, marginLeft: 4 }}>家</span>
                </span>
              </KpiTile>
            </Col>
            <Col xs={12} md={6}>
              <KpiTile title="最高板" note={`龙头 ${kpis?.leader_name ?? "--"}`}>
                <span>{kpis && kpis.max_streak > 0 ? `${kpis.max_streak}连板` : "--"}</span>
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
            <LimitUpLadder echelons={ladderEchelons} degraded={Boolean(data.degraded_reason)} />
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
            <StockTable data={stocks.data ?? []} loading={stocks.isLoading} />
          </SectionCard>
        ) : null}
      </Space>
    </div>
  );
}
