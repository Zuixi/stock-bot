import { Card, Spin, Typography } from "antd";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { fetchMarketMoneyflow, type MarketMoneyflowDay } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtSignedYi, fmtYi } from "./format";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;

const FLOW_ROWS: Array<{ key: "superLargeNet" | "largeNet" | "midNet" | "smallNet"; label: string }> = [
  { key: "superLargeNet", label: "超大单" },
  { key: "largeNet", label: "大单" },
  { key: "midNet", label: "中单" },
  { key: "smallNet", label: "小单" },
];

function buildHistoryOption(history: MarketMoneyflowDay[]) {
  const dates = history.map((h) => h.date.slice(5));
  const bars = history.map((h) => ({
    value: (h.mainNet ?? 0) / 1e8,
    pct: h.pctChange,
    itemStyle: { color: (h.mainNet ?? 0) >= 0 ? COLORS.up : COLORS.down, borderRadius: 1 },
  }));
  return {
    grid: { left: 8, right: 8, top: 14, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      formatter: (params: unknown) => {
        const arr = params as Array<{ name: string; data: { value: number; pct: number | null } }>;
        const p = arr?.[0];
        if (!p) return "";
        const color = p.data.value >= 0 ? COLORS.up : COLORS.down;
        return (
          `<div style="font-weight:600">${p.name}</div>` +
          `<div>主力净流入：<b style="color:${color}">${p.data.value.toFixed(2)}亿</b></div>` +
          `<div>上证涨跌幅：${p.data.pct == null ? "—" : `${p.data.pct.toFixed(2)}%`}</div>`
        );
      },
    },
    xAxis: { type: "category", data: dates, axisLabel: { fontSize: 10 } },
    yAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` }, splitLine: { lineStyle: { color: "#f0f0f0" } } },
    series: [
      {
        type: "bar",
        data: bars,
        barMaxWidth: 8,
        markLine: {
          silent: true,
          symbol: "none",
          lineStyle: { color: "#c9cdd4", type: "dashed" },
          data: [{ yAxis: 0 }],
          label: { show: false },
        },
      },
    ],
  };
}

/** 大盘资金流（沪深两市合成口径）：今日四档 + 近 30 日主力净流入。 */
export function MarketMoneyflowCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["market-moneyflow"],
    queryFn: fetchMarketMoneyflow,
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });
  const today = data?.today ?? null;
  const total = today?.total ?? null;
  const history = data?.history ?? [];
  const mainColor = (total?.mainNet ?? 0) > 0 ? COLORS.up : (total?.mainNet ?? 0) < 0 ? COLORS.down : COLORS.flat;

  return (
    <Card
      title="大盘资金流"
      size="small"
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>沪深两市 · 近30日</Typography.Text>}
    >
      <Spin spinning={isLoading}>
        {total && (
          <div style={{ marginBottom: 8 }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}>
              <Typography.Text type="secondary" style={{ fontSize: 12 }}>今日主力净流入</Typography.Text>
              <span style={{ fontSize: 22, fontWeight: 600, color: mainColor, fontVariantNumeric: "tabular-nums" }}>
                {fmtSignedYi(total.mainNet)}
              </span>
            </div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", marginTop: 2, fontSize: 12 }}>
              {FLOW_ROWS.map(({ key, label }) => (
                <span key={key}>
                  {label}
                  <b
                    style={{
                      fontVariantNumeric: "tabular-nums",
                      color: (total[key] ?? 0) > 0 ? COLORS.up : (total[key] ?? 0) < 0 ? COLORS.down : COLORS.flat,
                      marginLeft: 4,
                    }}
                  >
                    {fmtYi(total[key])}
                  </b>
                </span>
              ))}
            </div>
          </div>
        )}
        {history.length > 0 ? (
          <ReactECharts option={buildHistoryOption(history)} notMerge lazyUpdate style={{ height: history ? 168 : 200 }} />
        ) : (
          <div style={{ height: 200, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.flat }}>
            暂无大盘资金流数据（盘后自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
