import { Card, Segmented, Spin } from "antd";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import ReactECharts from "echarts-for-react";
import { fetchSectorMoneyflow, type SectorMoneyflowItem } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtYi } from "./format";

const STALE_TIME = 60 * 1000;
const REFETCH_INTERVAL = 60 * 1000;
const TOP_N = 10;

function buildOption(items: SectorMoneyflowItem[]) {
  const top = items.slice(0, TOP_N);
  const names = top.map((i) => i.boardName ?? i.boardCode).reverse();
  const bars = top
    .map((i) => ({
      value: (i.mainNetInflow ?? 0) / 1e8,
      pct: i.pctChange,
      ratio: i.mainNetRatio,
      itemStyle: { color: (i.mainNetInflow ?? 0) >= 0 ? COLORS.up : COLORS.down, borderRadius: 2 },
    }))
    .reverse();
  return {
    grid: { left: 8, right: 24, top: 8, bottom: 8, containLabel: true },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = (params as Array<{ name: string; data: { value: number; pct: number | null; ratio: number | null } }>)[0];
        const d = p?.data;
        if (!d) return "";
        return `<div style="font-weight:600">${p.name}</div>` +
          `<div>主力净流入：<b style="color:${d.value >= 0 ? COLORS.up : COLORS.down}">${d.value.toFixed(2)}亿</b></div>` +
          `<div>板块涨跌幅：${d.pct == null ? "—" : `${d.pct.toFixed(2)}%`}</div>` +
          `<div>主力净占比：${d.ratio == null ? "—" : `${d.ratio.toFixed(2)}%`}</div>`;
      },
    },
    xAxis: { type: "value", axisLabel: { formatter: (v: number) => `${v}亿` }, splitLine: { lineStyle: { color: "#f0f0f0" } } },
    yAxis: { type: "category", data: names, axisLabel: { width: 76, overflow: "truncate" } },
    series: [{ type: "bar", data: bars, barMaxWidth: 14 }],
  };
}

export function SectorMoneyflowCard() {
  const [dimension, setDimension] = useState<"industry" | "concept">("industry");
  const { data = [], isLoading } = useQuery({
    queryKey: ["sector-moneyflow", dimension],
    queryFn: () => fetchSectorMoneyflow(dimension),
    staleTime: STALE_TIME,
    refetchInterval: REFETCH_INTERVAL,
  });
  return (
    <Card
      title="板块主力资金流"
      size="small"
      extra={
        <Segmented
          size="small"
          value={dimension}
          onChange={(v) => setDimension(v as "industry" | "concept")}
          options={[
            { label: "行业", value: "industry" },
            { label: "概念", value: "concept" },
          ]}
        />
      }
    >
      <Spin spinning={isLoading}>
        {data.length > 0 ? (
          <ReactECharts option={buildOption(data)} notMerge lazyUpdate style={{ height: 260 }} />
        ) : (
          <div style={{ height: 260, display: "flex", alignItems: "center", justifyContent: "center", color: COLORS.flat }}>
            暂无资金流数据（交易日盘中自动更新）
          </div>
        )}
      </Spin>
    </Card>
  );
}
