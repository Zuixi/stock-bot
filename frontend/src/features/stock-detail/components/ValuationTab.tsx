import { Card, Row, Col, Segmented, Empty, Spin, Typography, Tooltip, Alert } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import ReactECharts from "echarts-for-react";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { fetchValuationHistory } from "@/shared/api/financial";
import type { ValuationMetric, ValuationRange } from "@/shared/api/financial";
import type { Exchange } from "@/shared/types";

interface Props {
  exchange: Exchange;
  symbol: string;
}

const METRICS: { label: string; value: ValuationMetric }[] = [
  { label: "PE(TTM)", value: "pe_ttm" },
  { label: "PE", value: "pe" },
  { label: "PB", value: "pb" },
  { label: "PS(TTM)", value: "ps_ttm" },
  { label: "PS", value: "ps" },
  { label: "股息率", value: "dividend_yield" },
];

const METRIC_LABELS: Record<string, string> = {
  pe_ttm: "PE(TTM)",
  pe: "PE",
  pb: "PB",
  ps_ttm: "PS(TTM)",
  ps: "PS",
  dividend_yield: "股息率",
};

const RANGES: { label: string; value: ValuationRange }[] = [
  { label: "1年", value: "1y" },
  { label: "3年", value: "3y" },
  { label: "5年", value: "5y" },
];

function useValuation(
  exchange: Exchange,
  symbol: string,
  metric: ValuationMetric,
  range: ValuationRange
) {
  return useQuery({
    queryKey: ["valuation-history", exchange, symbol, metric, range],
    queryFn: () => fetchValuationHistory(exchange, symbol, metric, range),
    enabled: Boolean(exchange && symbol),
  });
}

function SummaryStat({
  label,
  value,
  unit,
  tip,
}: {
  label: string;
  value: number | null | undefined;
  unit?: string;
  tip?: string;
}) {
  return (
    <Col xs={12} sm={6}>
      <Typography.Text type="secondary" style={{ fontSize: 12 }}>
        {label}
        {tip && (
          <Tooltip title={tip}>
            <InfoCircleOutlined style={{ marginLeft: 4, color: "#999", fontSize: 12 }} />
          </Tooltip>
        )}
      </Typography.Text>
      <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>
        {value == null || Number.isNaN(value)
          ? "--"
          : `${value.toLocaleString("zh-CN", { maximumFractionDigits: 2 })}${unit ?? ""}`}
      </div>
    </Col>
  );
}

function percentText(v: number | null | undefined): string {
  return v == null || Number.isNaN(v) ? "--" : `${v.toFixed(1)}%`;
}

export function ValuationTab({ exchange, symbol }: Props) {
  const [metric, setMetric] = useState<ValuationMetric>("pe_ttm");
  const [range, setRange] = useState<ValuationRange>("3y");

  const mainQuery = useValuation(exchange, symbol, metric, range);
  const peQuery = useValuation(exchange, symbol, "pe_ttm", "3y");
  const pbQuery = useValuation(exchange, symbol, "pb", "3y");
  const dyQuery = useValuation(exchange, symbol, "dividend_yield", "3y");

  const data = mainQuery.data;
  const periods = useMemo(
    () => data?.series.map((s) => s.trade_date) ?? [],
    [data]
  );
  const series = useMemo(
    () => data?.series.map((s) => s.value) ?? [],
    [data]
  );

  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const },
      grid: { left: 60, right: 20, top: 30, bottom: 40 },
      xAxis: { type: "category" as const, data: periods },
      yAxis: { type: "value" as const, scale: true },
      series: [
        {
          name: METRIC_LABELS[metric] ?? metric,
          type: "line" as const,
          smooth: true,
          showSymbol: false,
          data: series,
        },
      ],
    }),
    [periods, series, metric]
  );

  const currentPct = data?.percentiles?.[range] ?? null;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <Card size="small" title="估值摘要">
        <Row gutter={[16, 16]}>
          <SummaryStat
            label="PE(TTM) 当前值"
            value={peQuery.data?.current}
            tip="最新滚动 12 个月市盈率"
          />
          <SummaryStat
            label="PE(TTM) 3年分位"
            value={peQuery.data?.percentiles?.["3y"]}
            unit="%"
            tip="当前 PE 处于近 3 年的历史百分位"
          />
          <SummaryStat
            label="PB 3年分位"
            value={pbQuery.data?.percentiles?.["3y"]}
            unit="%"
            tip="当前 PB 处于近 3 年的历史百分位"
          />
          <SummaryStat
            label="当前股息率"
            value={dyQuery.data?.current}
            tip="按最近分红与股价计算"
          />
        </Row>
      </Card>

      <Card
        size="small"
        title="估值历史走势"
        extra={
          <div style={{ display: "flex", gap: 8 }}>
            <Segmented
              size="small"
              options={METRICS.map((m) => ({ label: m.label, value: m.value }))}
              value={metric}
              onChange={(v) => setMetric(v as ValuationMetric)}
            />
            <Segmented
              size="small"
              options={RANGES.map((r) => ({ label: r.label, value: r.value }))}
              value={range}
              onChange={(v) => setRange(v as ValuationRange)}
            />
          </div>
        }
      >
        {mainQuery.isLoading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : data && series.length ? (
          <>
            <ReactECharts option={option} style={{ height: 340 }} />
            {currentPct != null && (
              <Alert
                type="info"
                showIcon
                style={{ marginTop: 12 }}
                message={`当前处于 ${METRIC_LABELS[metric] ?? metric} ${rangeLabel(range)} 分位 ${currentPct.toFixed(1)}%`}
                description={`基于有效正样本计算；负值/缺失样本已排除。数据时间：${data.as_of ?? "--"}`}
              />
            )}
          </>
        ) : (
          <Empty description="暂无估值历史数据" />
        )}
      </Card>
    </div>
  );
}

function rangeLabel(range: ValuationRange): string {
  return range === "1y" ? "1年" : range === "3y" ? "3年" : "5年";
}
