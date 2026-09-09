import { useQuery } from "@tanstack/react-query";
import { fetchDistribution, fetchMarketIndices } from "@/shared/api/market";
import { useTheme } from "@/app/theme-context";
import type { MarketIndex } from "@/shared/types";

const REFRESH_MS = 60_000;

/** 优先展示的 A 股核心指数（接口缺谁就顺位补齐，恒定 8 格） */
const PREFERRED_TS_CODES = [
  "000001.SH", // 上证指数
  "399001.SZ", // 深证成指
  "399006.SZ", // 创业板指
  "000300.SH", // 沪深300
  "000905.SH", // 中证500
  "000688.SH", // 科创50
];

const TICKER_COUNT = 8;

/** 与市场页 DistributionChart 同一套分桶口径，保证宣传页与工作台数字一致 */
const UP_RANGES = ["1~3%", "3~5%", ">5%", "涨停"];
const DOWN_RANGES = ["0~-1%", "-1~-3%", "-3~-5%", "-5~-7%", ">-7%", "跌停"];

function pickIndices(list: MarketIndex[]): MarketIndex[] {
  const preferred = PREFERRED_TS_CODES
    .map((code) => list.find((i) => i.tsCode === code))
    .filter((i): i is MarketIndex => !!i);
  const rest = list.filter((i) => !PREFERRED_TS_CODES.includes(i.tsCode));
  return [...preferred, ...rest].slice(0, TICKER_COUNT);
}

function formatPrice(value: number): string {
  return value.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function Ticker({ index }: { index: MarketIndex }) {
  const { colors } = useTheme();
  const pct = index.changePercent;
  const color = pct > 0 ? colors.up : pct < 0 ? colors.down : colors.flat;
  const sign = pct > 0 ? "+" : "";

  return (
    <div className="landing-ticker">
      <div className="landing-ticker-name" title={index.name}>
        {index.name}
      </div>
      <div className="landing-ticker-price">{formatPrice(index.value)}</div>
      <span className="landing-ticker-chip" style={{ background: color }}>
        {sign}
        {pct.toFixed(2)}%
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

/** 实时脉搏卡：公开指数 + 涨跌分布摘要，60s 轮询；失败静默降级为占位文案 */
export function MarketPulse() {
  const { colors } = useTheme();

  const indicesQuery = useQuery({
    queryKey: ["market-indices"],
    queryFn: fetchMarketIndices,
    refetchInterval: REFRESH_MS,
  });

  const distQuery = useQuery({
    queryKey: ["market-distribution"],
    queryFn: fetchDistribution,
    refetchInterval: REFRESH_MS,
  });

  const indices = indicesQuery.data ? pickIndices(indicesQuery.data) : [];

  const up = distQuery.data
    ? distQuery.data.filter((d) => UP_RANGES.includes(d.range)).reduce((s, d) => s + d.count, 0)
    : null;
  const down =
    distQuery.data != null
      ? distQuery.data
          .filter((d) => DOWN_RANGES.includes(d.range))
          .reduce((s, d) => s + d.count, 0)
      : null;

  return (
    <div className="landing-pulse">
      <div className="landing-container">
        <div className="landing-pulse-card">
          <div className="landing-pulse-head">
            <span className="landing-pulse-title">
              <span className="landing-pulse-dot" />
              实时市场脉搏
            </span>
            <span className="landing-pulse-title">每 60 秒自动刷新</span>
          </div>

          {indicesQuery.isLoading ? (
            <TickerSkeletons />
          ) : indices.length > 0 ? (
            <div className="landing-ticker-row">
              {indices.map((idx) => (
                <Ticker key={idx.tsCode ?? idx.code} index={idx} />
              ))}
            </div>
          ) : (
            <div className="landing-placeholder">行情数据暂不可用，注册后可在工作台查看完整行情</div>
          )}

          <div className="landing-pulse-summary">
            <span>
              {up != null && down != null ? (
                <>
                  今日 A 股：上涨{" "}
                  <b style={{ color: colors.up, fontVariantNumeric: "tabular-nums" }}>
                    {up.toLocaleString()}
                  </b>{" "}
                  · 下跌{" "}
                  <b style={{ color: colors.down, fontVariantNumeric: "tabular-nums" }}>
                    {down.toLocaleString()}
                  </b>
                </>
              ) : indicesQuery.isLoading ? (
                "正在汇总今日涨跌分布…"
              ) : (
                "今日涨跌分布暂不可用"
              )}
            </span>
            <span>数据来源：TuShare / 东财，盘后为准</span>
          </div>
        </div>
      </div>
    </div>
  );
}
