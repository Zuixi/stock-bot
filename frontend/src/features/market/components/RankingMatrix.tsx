import { useState } from "react";
import { Empty, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DataRow, SectionCard } from "@/shared/ui";
import { fetchRankings } from "@/shared/api/market";
import type { RankingType } from "@/shared/api/market";

interface RankingTab {
  key: RankingType;
  label: string;
}

const TABS: RankingTab[] = [
  { key: "gainers", label: "涨幅榜" },
  { key: "losers", label: "跌幅榜" },
  { key: "amount", label: "成交额榜" },
  { key: "turnover_rate", label: "换手率榜" },
];

const TOP_N = 10;

/** `as_of`（YYYY-MM-DD）→ 「9月9日」样式。 */
function formatCnDate(iso: string): string {
  const [, month, day] = iso.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : iso;
}

/**
 * `daily_quotes.amount` 是 TuShare 原生 千元。沿用既有 mapper 口径：
 * ×1000 → 元，再按亿/万亿分档（不许在分支里「修」单位）。
 */
function formatAmount(amount: number | null | undefined): { value?: string; unit?: string } {
  if (amount == null) return {};
  const yuan = amount * 1e3;
  if (Math.abs(yuan) >= 1e12) return { value: (yuan / 1e12).toFixed(2), unit: "万亿元" };
  return { value: (yuan / 1e8).toFixed(2), unit: "亿元" };
}

/**
 * 首页榜单矩阵：四 Tab 切换，数据源为 `GET /market/rankings`（服务端按
 * `pct_chg IS NOT NULL` 过滤并锁定 top-N，每次请求 300s Redis 缓存）。
 *
 * 客户端 staleTime 放宽到 5 分钟避免首页轮询放大开销。失败/空态独立降级，
 * 不抛页面级异常。缺失涨跌幅一律由 DeltaText 渲染 `--`，不回退 0.00%。
 */
export function RankingMatrix() {
  const [active, setActive] = useState<RankingType>("gainers");

  const { data, isLoading, isError } = useQuery({
    queryKey: ["home", "ranking", active],
    queryFn: () => fetchRankings(active, TOP_N),
    staleTime: 5 * 60_000,
  });

  const rows = data?.items ?? [];

  return (
    <SectionCard id="rankings" title="今日榜单" moreHref="/market" moreText="进入行情页">
      <Tabs
        activeKey={active}
        onChange={(k) => setActive(k as RankingType)}
        items={TABS.map((t) => ({ key: t.key, label: t.label }))}
      />
      {data?.as_of ? (
        <div className="ranking__asof" style={{ fontSize: 12, color: "var(--text-secondary)" }}>
          数据截至 {formatCnDate(data.as_of)}
        </div>
      ) : null}
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
        rows.map((r) => {
          const amount = active === "amount" ? formatAmount(r.amount) : undefined;
          const value =
            active === "amount"
              ? amount?.value
              : active === "turnover_rate"
                ? (r.turnover_rate?.toFixed(2) ?? undefined)
                : (r.close?.toFixed(2) ?? undefined);
          const unit =
            active === "amount"
              ? amount?.unit
              : active === "turnover_rate"
                ? "%"
                : "元";
          return (
            <DataRow
              key={r.symbol}
              title={r.name}
              ticker={r.symbol}
              value={value}
              unit={unit}
              delta={r.pct_chg}
              href={`/stock/${r.symbol}`}
            />
          );
        })
      )}
    </SectionCard>
  );
}
