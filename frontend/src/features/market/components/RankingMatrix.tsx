import { useState } from "react";
import { Empty, Skeleton, Tabs } from "antd";
import { useQuery } from "@tanstack/react-query";
import { DataRow, DataRowList, SectionCard } from "@/shared/ui";
import { fetchRankings } from "@/shared/api/market";
import type { RankingType } from "@/shared/api/market";
import { fmtAmountParts } from "./format";
import { useMarketPolling } from "../hooks/useMarketPolling";

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

/**
 * 首页榜单矩阵：四 Tab 切换，数据源为 `GET /market/rankings`（服务端按
 * `pct_chg IS NOT NULL` 过滤并锁定 top-N，每次请求 300s Redis 缓存）。
 *
 * 客户端 staleTime 放宽到 5 分钟避免首页轮询放大开销。失败/空态独立降级，
 * 不抛页面级异常。缺失涨跌幅一律由 DeltaText 渲染 `--`，不回退 0.00%。
 */
export function RankingMatrix() {
  const [active, setActive] = useState<RankingType>("gainers");

  const { refetchInterval } = useMarketPolling();
  const { data, isLoading, isError } = useQuery({
    queryKey: ["market", "rankings", active],
    queryFn: () => fetchRankings(active, TOP_N),
    staleTime: 5 * 60_000,
    refetchInterval,
  });

  const rows = data?.items ?? [];

  return (
    <SectionCard
      id="rankings"
      title="今日榜单"
      moreHref="/market"
      moreText="进入行情页"
      asof={data?.asOf}
      quality={data?.asOfQuality}
    >
      <Tabs
        activeKey={active}
        onChange={(k) => setActive(k as RankingType)}
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
        /* 3 列密排（Phase 4 §5.3）：10 行 × 64px 的竖排在 1120px 容器里两端相距 1300px，
           改 3 列后同屏信息量 ×3、高度 ÷3（实测 694px → 约 300px），**一行数据都没删**。

           P1-2：行内数值槽改成**指标三元组**（最新价 → 涨跌额 → 涨跌幅，固定列宽 + 右对齐 +
           等宽数字），与自选/龙虎榜等标的清单同列宽，眼睛可以跨表竖读。`changePlaceholder="--"`
           让中间的「涨跌额」槽**占位**：`/market/rankings` 的 `RankingItemOut` 只回
           `close` / `pct_chg`（**没有** `change`/`pre_close`，实测载荷见 task-31-report），
           所以这一格现在如实显示 `--`——留空槽位比省掉一列好：槽位塌陷会让右边的涨跌幅列
           左右跳。补齐它需要后端在该端点加一个字段（前端不编造数字、不加新请求）。 */
        <DataRowList columns={3}>
          {rows.map((r) => {
            const amount = active === "amount" ? fmtAmountParts(r.amount) : undefined;
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
                valuePlaceholder="--"
                variant="triplet"
                changePlaceholder="--"
                delta={r.pct_chg}
                href={`/company/${r.symbol}`}
              />
            );
          })}
        </DataRowList>
      )}
    </SectionCard>
  );
}
