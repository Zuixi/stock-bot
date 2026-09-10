import { useState } from "react";
import { Empty, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DataRow, SectionCard } from "@/shared/ui";
import { fetchStockRanking } from "@/shared/api/stocks";
import type { RankingSortBy } from "@/shared/api/stocks";

type TabKey = "gainers" | "losers" | "amount";

interface RankingTab {
  key: TabKey;
  label: string;
  sortBy: RankingSortBy;
  order: "desc" | "asc";
}

/**
 * 三个 Tab 仅使用后端已支持的排序维度（changePercent / turnover）。
 * 换手率榜需后端新增排序字段，Phase 2 Task 2.7 随 /market/rankings 一并上线。
 */
const TABS: RankingTab[] = [
  { key: "gainers", label: "涨幅榜", sortBy: "changePercent", order: "desc" },
  { key: "losers", label: "跌幅榜", sortBy: "changePercent", order: "asc" },
  { key: "amount", label: "成交额榜", sortBy: "turnover", order: "desc" },
];

const TOP_N = 10;

/**
 * 多取一批再截断：服务端 desc 排序把 null 排在最前（`(is None, value)` 键反转），
 * 无行情的次新股会霸占涨幅榜首屏。前端按排序维度剔掉不可排序行后仍能凑满 top-N。
 * Task 2.7 切到 `/market/rankings`（SQL 已 `pct_chg IS NOT NULL`）后此守卫是保险，非终局修复。
 */
const FETCH_LIMIT = TOP_N * 2;

/**
 * 首页榜单矩阵：三 Tab 切换、每 Tab 共享同一查询缓存键前缀。
 *
 * 走 Task 1.3 的 enriched 排序端点（服务端 60s 缓存），客户端 staleTime 放宽到
 * 5 分钟避免首页轮询放大开销。失败/空态独立降级，不抛页面级异常。
 */
export function RankingMatrix() {
  const [active, setActive] = useState<TabKey>("gainers");
  const tab = TABS.find((t) => t.key === active) ?? TABS[0];

  const { data, isLoading, isError } = useQuery({
    queryKey: ["home", "ranking", active],
    queryFn: () => fetchStockRanking(tab.sortBy, tab.order, FETCH_LIMIT),
    staleTime: 5 * 60_000,
  });

  const isAmountTab = tab.key === "amount";
  // 按当前排序维度剔除不可排序行（涨跌幅榜 → changePercent null，成交额榜 → amount null）
  const rows = (data ?? [])
    .filter((r) => (isAmountTab ? r.amount != null : r.changePercent != null))
    .slice(0, TOP_N);

  return (
    <SectionCard id="rankings" title="今日榜单" moreHref="/market" moreText="进入行情页">
      <Tabs
        activeKey={active}
        onChange={(k) => setActive(k as TabKey)}
        items={TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      {isLoading ? (
        <Skeleton active paragraph={{ rows: 6 }} />
      ) : isError ? (
        <div
          style={{
            padding: "24px 0",
            textAlign: "center",
            color: "var(--text-secondary)",
            fontSize: 13,
          }}
        >
          榜单数据暂不可用，请稍后重试
        </div>
      ) : rows.length === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无榜单数据" />
      ) : (
        rows.map((r) => (
          <DataRow
            key={r.symbol}
            title={r.name}
            ticker={r.symbol}
            value={
              isAmountTab
                ? r.amount == null
                  ? undefined
                  : (r.amount / 1e8).toFixed(2)
                : r.latestPrice?.toFixed(2)
            }
            unit={isAmountTab ? "亿元" : "元"}
            delta={r.changePercent}
            href={`/stock/${r.symbol}`}
          />
        ))
      )}
      {/* Phase 2 上线后此处追加：数据截至 {as_of}（Task 2.7） */}
    </SectionCard>
  );
}
