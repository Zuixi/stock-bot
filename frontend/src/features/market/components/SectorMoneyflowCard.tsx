import { Card, Segmented, Spin, Tooltip } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchSectorMoneyflow,
  fetchSectorTrends,
  type SectorDimension,
  type SectorMoneyflowItem,
  type SectorTrends,
} from "@/shared/api/marketData";
import { EChart } from "@/shared/ui/EChart";
import { FreshnessNote } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { ThemePalette } from "@/app/theme";
import { useMarketPolling } from "../hooks/useMarketPolling";
import "./SectorMoneyflowCard.css";

const STALE_TIME = 60 * 1000;
const TOP_N = 10;
/** 连续性 chip 展示条数：与上方柱状图的前几条对齐（后端按末完整日榜序返回）。 */
const CONTINUITY_CHIPS = 5;
/** 缺完整日最多逐日列出几个（计数永远是全量；窗口上限 120 天，全列会撑爆卡片）。 */
const MISSING_DAYS_SHOWN = 8;

function buildOption(items: SectorMoneyflowItem[], c: ThemePalette) {
  const top = items.slice(0, TOP_N);
  const names = top.map((i) => i.boardName ?? i.boardCode).reverse();
  const bars = top
    .map((i) => ({
      // 同 MarketMoneyflowCard：`mainNetInflow` 可空，折成 0 会画出一根与"真实 0 亿"
      // 无法区分的零高度柱。透传 null → 该板块**不画柱**，tooltip 的
      // `if (!d || d.value == null) return ""` 分支也才真正生效。
      value: i.mainNetInflow == null ? null : i.mainNetInflow / 1e8,
      pct: i.pctChange,
      ratio: i.mainNetRatio,
      leadName: i.leadStockName,
      leadPct: i.leadStockPct,
      // 颜色与 value 同口径（与 MarketMoneyflowCard 逐字一致）：null 时柱子本来就不画，
      // 但 `(x ?? 0) >= 0` 会把"缺值"折成 0 再判成 `c.up` —— 留一个"不可见但语义错误"的
      // 分支；一旦哪天改成透明占位柱，它会立刻以"阳线色"露出来。故显式判 null → `c.flat`。
      itemStyle: {
        color: i.mainNetInflow == null ? c.flat : i.mainNetInflow >= 0 ? c.up : c.down,
        borderRadius: 2,
      },
    }))
    .reverse();
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = (
          params as Array<{
            name: string;
            data: {
              value: number | null;
              pct: number | null;
              ratio: number | null;
              leadName: string | null;
              leadPct: number | null;
            };
          }>
        )[0];
        const d = p?.data;
        if (!d || d.value == null) return "";
        return `<div style="font-weight:600">${p.name}</div>` +
          `<div>主力净流入：<b style="color:${d.value >= 0 ? c.up : c.down}">${d.value.toFixed(2)}亿</b></div>` +
          `<div>板块涨跌幅：${d.pct == null ? "—" : `${d.pct.toFixed(2)}%`}</div>` +
          `<div>主力净占比：${d.ratio == null ? "—" : `${d.ratio.toFixed(2)}%`}</div>` +
          `<div>主力净流入最大股：${d.leadName ?? "—"}${d.leadPct == null ? "" : `（${d.leadPct > 0 ? "+" : ""}${d.leadPct.toFixed(2)}%）`}</div>`;
      },
    },
    xAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` } },
    yAxis: { type: "category", data: names, axisLabel: { width: 76, overflow: "truncate" } },
    series: [{ type: "bar", data: bars, barMaxWidth: 14 }],
  };
}

/**
 * 末完整日主力净流入（元）→ 亿元；`null` = 该行上游缺值 → `--`（**绝不** 0 流入）。
 * 本区块统一用 `--`（Task 7 契约）而不是 format.ts 的 `—`。
 */
function lastInflow(value: number | null): string {
  if (value == null) return "--";
  return `${value > 0 ? "+" : ""}${(value / 1e8).toFixed(2)}亿`;
}

/**
 * 板块连续性 chip（Task 7）。
 *
 * 三条硬约束（Ruling 21/22）：
 * 1. 连续性只在**完整日**上累积（后端算），前端**不重算**、**不插值**；
 * 2. `attributionNote`（行业/地域 = 按当前分类回溯，非当时成分）与 `availableFrom`
 *    （自 X 起累积）必须上屏 —— 归属口径不能藏在代码里；
 * 3. 缺完整日（`missingDays`）按缺日披露（不计 0 流入）；末日缺值渲染 `--`；
 *    `luCountSource=unavailable` 时**不显示**家数（缺失 ≠ 0 家）。
 *
 * Task 8 补充：表头并排显示**请求窗口**（`window.days`）与**实际观测跨度**
 * （`window.observedDays`）—— 只显示 `samples`/观测值会让"数据起点附近只取到 5 个交易日"
 * 被读成"用户只请求了 5 天"。
 */
function ContinuityStrip({ trends }: { trends: SectorTrends }) {
  const boards = trends.boards.slice(0, CONTINUITY_CHIPS);
  const luAvailable = trends.luCountSource === "point_in_time_membership";
  const { days, observedDays, from, to, samples } = trends.window;
  return (
    <div className="mf-continuity" data-testid="sector-continuity">
      <div className="mf-continuity__head">
        <span className="mf-continuity__title">
          板块连续性（近 {samples} 个完整日 · 上榜口径 Top {trends.topN}）
        </span>
        {/* 请求值 vs 实际观测跨度必须并排：只显示观测值会把"短观测"读成"短请求" */}
        <span className="mf-continuity__tag" data-testid="sector-continuity-window">
          请求近 {days} 交易日 · 实际观察 {observedDays} 个交易日
          {from && to ? `（${from} → ${to}）` : ""}
        </span>
        <span className="mf-continuity__tag" data-testid="sector-continuity-attribution">
          {trends.attributionNote}
        </span>
        <span className="mf-continuity__tag" data-testid="sector-continuity-available-from">
          {trends.availableFrom ? `自 ${trends.availableFrom} 起累积` : "尚无完整日，连续性无法累积"}
        </span>
      </div>

      {boards.length > 0 ? (
        <div className="mf-continuity__chips">
          {boards.map((b) => {
            const last = b.series[b.series.length - 1];
            const luCount = luAvailable ? (last?.luCount ?? null) : null;
            return (
              <span
                className="mf-continuity__chip num"
                key={b.boardCode}
                data-testid={`sector-continuity-${b.boardCode}`}
              >
                <span className="mf-continuity__name">{b.boardName ?? b.boardCode}</span>
                {b.streakOnTopN > 0 ? (
                  <span className="mf-continuity__stat">连续 {b.streakOnTopN} 天上榜</span>
                ) : null}
                {b.streakInflow > 0 ? (
                  <span className="mf-continuity__stat mf-continuity__stat--in">
                    连续 {b.streakInflow} 天净流入
                  </span>
                ) : null}
                {b.streakOutflow > 0 ? (
                  <span className="mf-continuity__stat mf-continuity__stat--out">
                    连续 {b.streakOutflow} 天净流出
                  </span>
                ) : null}
                <span className="mf-continuity__stat">末完整日 {lastInflow(b.mainNetInflow)}</span>
                {luCount != null ? <span className="mf-continuity__stat">当日涨停 {luCount} 家</span> : null}
              </span>
            );
          })}
        </div>
      ) : (
        <div className="mf-continuity__empty">
          暂无连续性数据（连续性只在完整日上累积；缺完整日的日子不进统计）
        </div>
      )}

      {trends.missingDays.length > 0 ? (
        <div className="mf-continuity__missing" data-testid="sector-continuity-missing">
          缺完整日 {trends.missingDays.length} 天（不计 0 流入）：
          {trends.missingDays.slice(0, MISSING_DAYS_SHOWN).join("、")}
          {trends.missingDays.length > MISSING_DAYS_SHOWN ? " …" : ""}
        </div>
      ) : null}

      {trends.leaderChangeCount > 0 ? (
        <div className="mf-continuity__leaders">窗口内龙头更替 {trends.leaderChangeCount} 次</div>
      ) : null}
    </div>
  );
}

export interface SectorMoneyflowCardProps {
  /**
   * `compact`（P1-1 第二轮）：把「板块连续性」区收进一个**默认收起**的折叠开关。
   *
   * 为什么需要它（实测，不是审美）：1440 下本卡高 **656px**，同排的涨跌分布 / 热力图只有
   * **352 / 382px**（差 304px），多出来的**全部**是连续性区（`sector-continuity` 的高度）。
   * 三块并排时这一块像"多长了一条腿"。`compact` 下连续性区**一字不删**：展开后与 `full`
   * 变体逐字相同（同一 `ContinuityStrip`、同一口径标注），只是默认不占版面。
   * `/market` 的 A股全景分区用 `compact`；其它调用方（若将来有）默认仍是 `full`。
   */
  variant?: "full" | "compact";
}

export function SectorMoneyflowCard({ variant = "full" }: SectorMoneyflowCardProps) {
  const { colors } = useTheme();
  const [dimension, setDimension] = useState<SectorDimension>("industry");
  const [showContinuity, setShowContinuity] = useState(false);
  const { refetchInterval } = useMarketPolling();
  const { data: moneyflow, isLoading } = useQuery({
    queryKey: ["market", "sector-moneyflow", dimension],
    queryFn: () => fetchSectorMoneyflow(dimension),
    staleTime: STALE_TIME,
    refetchInterval,
  });
  // 板块趋势（读时计算，端点侧 Redis 缓存 300s）：query key 带 dimension —— 三个维度的
  // 归属口径（当前申万快照 vs 概念时点成分）不可共用缓存条目。
  const { data: trends } = useQuery({
    queryKey: ["market", "sector-trends", dimension],
    queryFn: () => fetchSectorTrends(dimension),
    staleTime: 300_000,
    refetchInterval,
  });
  const data = moneyflow?.items ?? [];
  return (
    <Card
      title="板块主力资金流"
      size="small"
      extra={
        <Segmented
          size="small"
          value={dimension}
          onChange={(v) => setDimension(v as SectorDimension)}
          options={[
            { label: "行业", value: "industry" },
            {
              // 口径声明挂在概念维度本身：东财概念资金流是按净流入排序的 Top100 样本，
              // 不是全量约 500 个概念板块 —— 不声明就会让「板块榜」被读成全量。
              label: (
                <Tooltip title="主力资金流为按净流入排序的 Top100 样本，非全量板块">
                  <span>概念</span>
                </Tooltip>
              ),
              value: "concept",
            },
            { label: "地域", value: "region" },
          ]}
        />
      }
    >
      <Spin spinning={isLoading}>
        <FreshnessNote asOf={moneyflow?.asOf} staleDays={moneyflow?.staleDays} />
        {data.length > 0 ? (
          <EChart option={buildOption(data, colors)} height={260} />
        ) : (
          <div style={{ height: 260, display: "flex", alignItems: "center", justifyContent: "center", color: colors.flat }}>
            暂无资金流数据（交易日盘中自动更新）
          </div>
        )}
        {/* compact：连续性区收进一行折叠开关（默认收起）。开关本身不念数，
            只说明"这里有什么 + 窗口多大"，读数一律留在展开后的 ContinuityStrip 里
            （同一事实不写两处，避免折叠态与展开态数字打架）。 */}
        {variant === "compact" && trends ? (
          <button
            type="button"
            className="mf-continuity-toggle"
            data-testid="sector-continuity-toggle"
            aria-expanded={showContinuity}
            onClick={() => setShowContinuity((open) => !open)}
          >
            <span className="mf-continuity-toggle__caret" aria-hidden="true">
              {showContinuity ? "▾" : "▸"}
            </span>
            <span className="mf-continuity-toggle__title">板块连续性</span>
            <span className="mf-continuity-toggle__hint">
              近 {trends.window.samples} 个完整日 · 上榜口径 Top {trends.topN}
            </span>
            <span className="mf-continuity-toggle__action">{showContinuity ? "收起" : "展开"}</span>
          </button>
        ) : null}
        {trends && (variant === "full" || showContinuity) ? <ContinuityStrip trends={trends} /> : null}
      </Spin>
    </Card>
  );
}
