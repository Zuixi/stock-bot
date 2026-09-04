import ReactECharts from "echarts-for-react";
import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import { fetchDistribution } from "@/shared/api/market";
import { fetchMarketMoneyflow } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";

const STALE_TIME = 5 * 60 * 1000;

/** 桶位中值（与后端 get_distribution 分桶一一对应），驱动强度渐变与涨/平/跌聚合。 */
const BUCKETS: Array<{ label: string; mid: number; flat?: boolean }> = [
  { label: "跌停", mid: -10 },
  { label: ">-7%", mid: -8.25 },
  { label: "-5~-7%", mid: -6 },
  { label: "-3~-5%", mid: -4 },
  { label: "-1~-3%", mid: -2 },
  { label: "0~-1%", mid: -0.5 },
  { label: "0~1%", mid: 0.5, flat: true },
  { label: "1~3%", mid: 2 },
  { label: "3~5%", mid: 4 },
  { label: ">5%", mid: 7.25 },
  { label: "涨停", mid: 10 },
];

export function hexLerp(a: string, b: string, t: number): string {
  const pa = [1, 3, 5].map((i) => parseInt(a.slice(i, i + 2), 16));
  const pb = [1, 3, 5].map((i) => parseInt(b.slice(i, i + 2), 16));
  const c = pa.map((v, i) => Math.round(v + (pb[i] - v) * t));
  return `#${c.map((v) => v.toString(16).padStart(2, "0")).join("")}`;
}

function bucketColor(mid: number, flat?: boolean): string {
  if (flat) return "#cbd5e1";
  const t = Math.min(1, 0.3 + Math.abs(mid) / 10); // 越极端越深
  return mid > 0 ? hexLerp("#fecaca", "#dc2626", t) : hexLerp("#bbf7d0", "#16a34a", t);
}

/** 涨跌平衡条：红/灰/绿长度按家数比例——一秒读出市场多空比。 */
function BalanceBar({ up, flat, down }: { up: number; flat: number; down: number }) {
  const total = up + flat + down || 1;
  const seg = (n: number) => `${((n / total) * 100).toFixed(2)}%`;
  return (
    <div style={{ marginTop: 10 }}>
      <div style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden" }}>
        <div style={{ width: seg(up), background: COLORS.up }} title={`上涨 ${up} 只`} />
        <div style={{ width: seg(flat), background: "#cbd5e1" }} title={`平/微涨 ${flat} 只`} />
        <div style={{ width: seg(down), background: COLORS.down }} title={`下跌 ${down} 只`} />
      </div>
      <div style={{ display: "flex", justifyContent: "space-between", fontSize: 12, marginTop: 4 }}>
        <Typography.Text type="secondary">
          上涨 <b style={{ color: COLORS.up, fontVariantNumeric: "tabular-nums" }}>{up.toLocaleString()}</b> 只
        </Typography.Text>
        <Typography.Text type="secondary">
          下跌 <b style={{ color: COLORS.down, fontVariantNumeric: "tabular-nums" }}>{down.toLocaleString()}</b> 只
        </Typography.Text>
      </div>
    </div>
  );
}

export function DistributionChart() {
  const { data = [], isLoading } = useQuery({
    queryKey: ["market-distribution"],
    queryFn: fetchDistribution,
    staleTime: STALE_TIME,
  });
  // 成交额复用大盘资金流的实时查询（同 queryKey 共享缓存与轮询）
  const { data: mm } = useQuery({
    queryKey: ["market-moneyflow"],
    queryFn: fetchMarketMoneyflow,
    staleTime: 60 * 1000,
    refetchInterval: 60 * 1000,
  });
  const amount = mm?.today?.total?.amount ?? null;

  const counts = Object.fromEntries(data.map((d) => [d.range, d.count]));
  const get = (label: string) => counts[label] ?? 0;
  const up = get("1~3%") + get("3~5%") + get(">5%") + get("涨停");
  const flat = get("0~1%");
  const down =
    get("0~-1%") + get("-1~-3%") + get("-3~-5%") + get("-5~-7%") + get(">-7%") + get("跌停");
  const total = up + flat + down || 1;

  const option = {
    tooltip: {
      trigger: "axis" as const,
      formatter: (params: any) => {
        const p = params[0];
        const pct = total ? ((p.value / total) * 100).toFixed(1) : "0";
        return `${p.name}<br/>数量: <b>${p.value}</b>（占 ${pct}%）`;
      },
    },
    grid: { left: 40, right: 16, top: 24, bottom: 32 },
    xAxis: {
      type: "category" as const,
      data: data.map((d) => d.range),
      axisLabel: { fontSize: 10, rotate: 30 },
    },
    yAxis: { type: "value" as const, splitLine: { lineStyle: { type: "dashed" as const } } },
    series: [
      {
        type: "bar" as const,
        data: data.map((d) => {
          const meta = BUCKETS.find((b) => b.label === d.range);
          return {
            value: d.count,
            itemStyle: { color: bucketColor(meta?.mid ?? 0, meta?.flat), borderRadius: 2 },
          };
        }),
        barMaxWidth: 36,
      },
    ],
  };

  return (
    <Card
      title="A股涨跌分布"
      size="small"
      extra={
        <Typography.Text type="secondary" style={{ fontSize: 12 }}>
          成交额 {amount == null ? "—" : `${(amount / 1e12).toFixed(2)} 万亿`}
        </Typography.Text>
      }
    >
      <Spin spinning={isLoading}>
        <ReactECharts option={option} style={{ height: 220 }} />
        <BalanceBar up={up} flat={flat} down={down} />
      </Spin>
    </Card>
  );
}
