/**
 * A股排行 7 榜 `/market/rankings/:key`（spec §S1.6）。
 *
 * **公开免登录**：`/market/**` 不套 `RequireAuth`（ADR 0008）。
 *
 * 页面只做三件事：选榜（Tab）→ 取数 → 把**后端下发的口径**如实上屏。
 * 口径三件事（交易日 / 财务期 / 样本范围）与「不足 100 条的原因」全部来自 payload
 * 的 `caliber` / `notes`：前端**不得**自己写"用的是最新一期"这类文案
 * ——那句话的正确与否取决于 SQL，不取决于 UI。也正因如此，这里没有任何
 * 硬编码的阈值/期数。
 */
import { useMemo } from "react";
import { Link, Navigate, useNavigate, useParams } from "react-router-dom";
import { Alert, Breadcrumb, Card, Descriptions, Skeleton, Space, Tabs, Tag, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { SectionCard } from "@/shared/ui";
import {
  fetchRankingBoard,
  isRankingBoardKey,
  RANKING_BOARD_LIMIT,
  RANKING_BOARD_TABS,
  RANKING_CATEGORY_LABEL,
  type RankingBoardKey,
} from "@/features/market/rankings";
import { useMarketPolling } from "@/features/market/hooks/useMarketPolling";
import { RankingBoardTable } from "./RankingBoardTable";

const { Paragraph } = Typography;

/** 口径三件事的固定标签（spec §S1.6 逐字要求的三项）。 */
const CALIBER_LABELS: ReadonlyArray<{ key: "tradeDate" | "financialPeriod" | "sampleScope"; label: string }> = [
  { key: "tradeDate", label: "① 以哪个交易日为准" },
  { key: "financialPeriod", label: "② 用的是哪期财务数据" },
  { key: "sampleScope", label: "③ 是否含次新股 / ST" },
];

export default function MarketRankingsPage() {
  const { key } = useParams<{ key: string }>();
  const navigate = useNavigate();
  const { refetchInterval } = useMarketPolling();

  const activeKey: RankingBoardKey | null = isRankingBoardKey(key) ? key : null;

  const { data, isLoading, isError } = useQuery({
    queryKey: ["market", "ranking-board", activeKey],
    queryFn: () => fetchRankingBoard(activeKey as RankingBoardKey, RANKING_BOARD_LIMIT),
    // 榜单是收盘口径的确定性结果：不跟着盘中轮询抖动，但仍允许 5 分钟后自动刷新
    // （缓存键带口径日，翻日/补数据时新键自然取新值）。
    staleTime: 5 * 60_000,
    refetchInterval,
    enabled: activeKey !== null,
  });

  const tabItems = useMemo(
    () =>
      (["valuation", "price", "financial"] as const).flatMap((category) =>
        RANKING_BOARD_TABS.filter((tab) => tab.category === category).map((tab) => ({
          key: tab.key,
          label: tab.label,
        })),
      ),
    [],
  );

  if (activeKey === null) {
    // 未知 key（手输/旧书签）：回到第一个榜，而不是渲染一个空白页
    return <Navigate to={`/market/rankings/${RANKING_BOARD_TABS[0].key}`} replace />;
  }

  const caliber = data?.caliber;
  const fewerThanLimit = data !== undefined && data.returnedRows < data.limit;

  return (
    <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <Breadcrumb
        items={[
          { title: <Link to="/market">市场</Link> },
          { title: "A股排行" },
          { title: data?.label ?? RANKING_BOARD_TABS.find((t) => t.key === activeKey)?.label },
        ]}
      />

      <Tabs
        activeKey={activeKey}
        onChange={(next) => navigate(`/market/rankings/${next}`)}
        items={tabItems}
      />

      <SectionCard
        id="ranking-board"
        title={data?.label ? `${data.label} 100 强` : "榜单"}
        asof={data?.asOf}
        quality={data?.asOfQuality}
      >
        {isError ? (
          <Alert
            type="warning"
            showIcon
            message="榜单数据暂不可用"
            description="请稍后重试；本页不提供本地缓存或估算值。"
          />
        ) : isLoading || !data ? (
          <Skeleton active paragraph={{ rows: 8 }} />
        ) : (
          <Space direction="vertical" size={12} style={{ width: "100%" }}>
            <Space size={8} wrap>
              <Tag color={data.category === "financial" ? "purple" : data.category === "price" ? "blue" : "cyan"}>
                {RANKING_CATEGORY_LABEL[data.category]}
              </Tag>
              <Tag>
                共 {data.eligibleRows} 只可参与排序 / 返回 {data.returnedRows} 条
              </Tag>
              {!data.isLatestTradingDay && <Tag color="orange">非最新交易日</Tag>}
              {data.truncatedTies && <Tag color="gold">榜尾并列</Tag>}
            </Space>

            {data.asOfReason && (
              // `as_of_reason` 是后端完整性判据的原样判别式（如 latest_day_incomplete）；
              // 原样上屏而不是换个更"好看"的说法——它是口径的一部分。
              <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
                口径日判据：{data.asOfReason}（as_of_quality = {data.asOfQuality}）
              </Paragraph>
            )}

            {fewerThanLimit && (
              <Alert
                type="info"
                showIcon
                message={`不足 ${data.limit} 条`}
                description={
                  <ul style={{ margin: 0, paddingInlineStart: 18 }}>
                    {data.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                }
              />
            )}
            {!fewerThanLimit && data.notes.length > 0 && (
              <Alert
                type="info"
                showIcon
                message="口径补充"
                description={
                  <ul style={{ margin: 0, paddingInlineStart: 18 }}>
                    {data.notes.map((note) => (
                      <li key={note}>{note}</li>
                    ))}
                  </ul>
                }
              />
            )}

            <Card size="small" title="榜单口径">
              <Descriptions column={1} size="small" bordered>
                {CALIBER_LABELS.map(({ key: field, label }) => (
                  <Descriptions.Item key={field} label={label}>
                    <Paragraph style={{ margin: 0, whiteSpace: "pre-wrap" }}>
                      {caliber?.[field] ?? "--"}
                    </Paragraph>
                  </Descriptions.Item>
                ))}
              </Descriptions>
            </Card>

            <RankingBoardTable
              items={data.items}
              sortDirection={data.sortDirection}
              onRowClick={(symbol) => navigate(`/company/${symbol}`)}
            />

            <Paragraph type="secondary" style={{ margin: 0, fontSize: 12 }}>
              数值单位随榜（后端下发）：{data.metricUnit}。缺失一律显示「--」，**不以 0 顶替**
              ——财务字段为空表示"该交易日尚无已公告期"，与"利润为 0"是两件事。
            </Paragraph>
          </Space>
        )}
      </SectionCard>
    </div>
  );
}
