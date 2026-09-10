import { useQuery } from "@tanstack/react-query";
import { fetchDistribution } from "@/shared/api/market";
import { fetchGlobalIndices } from "@/shared/api/marketData";
import type { GlobalIndexCard } from "@/shared/api/marketData";
import { DistributionBars } from "@/features/market/components/DistributionBars";
import type { DistributionBucket } from "@/features/market/components/DistributionBars";
import { pickCoreIndices } from "@/features/market/components/coreIndices";
import { useTheme } from "@/app/theme-context";

const REFRESH_MS = 60_000;

/** 恒定 8 格：优先核心 6 码，缺谁用任意市场的其余指数顺位补齐（共享选择器） */
const TICKER_COUNT = 8;

/**
 * 与市场页 DistributionChart 同一套分桶口径，保证宣传页与工作台数字一致。
 * 两侧必须对称且穷尽 11 桶：`0~1%` 归上涨、`0~-1%` 归下跌——旧实现漏掉 `0~1%`，
 * 会把该桶 900+ 只个股从「上涨」家数里静默丢掉。
 */
const UP_RANGES = ["0~1%", "1~3%", "3~5%", ">5%", "涨停"];
const DOWN_RANGES = ["跌停", ">-7%", "-5~-7%", "-3~-5%", "-1~-3%", "0~-1%"];

function directionOf(range: string): DistributionBucket["direction"] {
  if (UP_RANGES.includes(range)) return "up";
  if (DOWN_RANGES.includes(range)) return "down";
  return "flat";
}

function formatPrice(value: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Ticker({ index }: { index: GlobalIndexCard }) {
  const { colors } = useTheme();
  const pct = index.pctChange;
  // 涨跌幅缺失（EOD 兜底且 spark 不足）时与价格同口径显示 "--"，不得回退成
  // 0.00%（会被读成"平盘"，与 data 缺失语义相悖）
  const color = pct == null || pct === 0 ? colors.flat : pct > 0 ? colors.up : colors.down;

  return (
    <div className="landing-ticker index-ticker">
      <div className="landing-ticker-name" title={index.name}>
        {index.name}
      </div>
      <div className="landing-ticker-price">
        {index.price != null ? formatPrice(index.price) : "--"}
      </div>
      <span className="landing-ticker-chip" style={{ background: color }}>
        {pct != null ? `${pct > 0 ? "+" : ""}${pct.toFixed(2)}%` : "--"}
      </span>
    </div>
  );
}

function TickerSkeletons() {
  return (
    <div className="landing-ticker-row">
      {Array.from({ length: TICKER_COUNT }, (_, i) => (
        <div key={i} className="landing-skeleton" style={{ height: 92 }} />
      ))}
    </div>
  );
}

/**
 * 实时脉搏卡内容：公开指数 8 格 + 涨跌分布柱 + 家数汇总，60s 轮询。
 *
 * 免登录可读；指数与分布两条查询各自降级，任一失败不影响另一块，也不抛到页面级
 * ErrorBoundary。外层由首页 `<SectionCard id="pulse">` 提供卡片壳与标题。
 */
export function MarketPulse() {
  const { colors } = useTheme();

  const indicesQuery = useQuery({
    queryKey: ["global-indices"],
    queryFn: fetchGlobalIndices,
    refetchInterval: REFRESH_MS,
  });

  const distQuery = useQuery({
    queryKey: ["market-distribution"],
    queryFn: fetchDistribution,
    refetchInterval: REFRESH_MS,
  });

  const indices = indicesQuery.data ? pickCoreIndices(indicesQuery.data, TICKER_COUNT) : [];

  const dist = distQuery.data ?? [];
  // 空数组不是「全平盘」而是「分布不可用」——缺失与零值语义不同，不能渲染成 上涨 0 · 下跌 0
  const hasDistribution = dist.length > 0;

  const buckets: DistributionBucket[] = hasDistribution
    ? dist.map((d) => ({
        label: d.range,
        count: d.count,
        direction: directionOf(d.range),
      }))
    : [];

  const sumOf = (ranges: string[]) =>
    dist.filter((d) => ranges.includes(d.range)).reduce((s, d) => s + d.count, 0);

  const up = hasDistribution ? sumOf(UP_RANGES) : null;
  const down = hasDistribution ? sumOf(DOWN_RANGES) : null;

  return (
    <div className="landing-pulse-body">
      {indicesQuery.isLoading ? (
        <TickerSkeletons />
      ) : indices.length > 0 ? (
        <>
          <div className="landing-ticker-row">
            {indices.map((idx) => (
              <Ticker key={idx.tsCode} index={idx} />
            ))}
          </div>
          {/* 「实时 + 60s 刷新」只描述指数条；下方分布是当日收盘（T+1）口径 */}
          <div className="landing-pulse-caption">指数实时快照 · 每 60 秒自动刷新</div>
        </>
      ) : (
        <div className="landing-placeholder">行情数据暂不可用，请稍后重试</div>
      )}

      {buckets.length > 0 ? (
        <div className="landing-pulse-dist">
          <div className="landing-pulse-dist-title">当日涨跌分布（日度 / T+1）</div>
          <DistributionBars buckets={buckets} />
        </div>
      ) : null}

      <div className="landing-pulse-summary">
        <span>
          {up != null && down != null ? (
            <>
              当日 A 股（T+1）：上涨{" "}
              <b style={{ color: colors.up, fontVariantNumeric: "tabular-nums" }}>
                {up.toLocaleString()}
              </b>{" "}
              · 下跌{" "}
              <b style={{ color: colors.down, fontVariantNumeric: "tabular-nums" }}>
                {down.toLocaleString()}
              </b>
            </>
          ) : distQuery.isLoading ? (
            "正在汇总当日涨跌分布…"
          ) : (
            "当日涨跌分布暂不可用"
          )}
        </span>
        <span>涨跌分布为当日收盘口径（T+1），与市场页同源</span>
      </div>
    </div>
  );
}
