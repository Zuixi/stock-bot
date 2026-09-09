import { Card, Row, Col, Segmented, Empty, Spin, Tooltip, Table, Typography, Alert } from "antd";
import ReactECharts from "echarts-for-react";
import { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchFinancialSummary,
  fetchFinancialStatements,
  fetchFinancialMetricsHistory,
} from "@/shared/api/financial";
import type { FinancialSummary, MetricHistory } from "@/shared/api/financial";
import type { Exchange } from "@/shared/types";

interface Props {
  exchange: Exchange;
  symbol: string;
}

const PERIOD_COUNTS = [
  { label: "4期", value: 4 },
  { label: "8期", value: 8 },
  { label: "12期", value: 12 },
  { label: "20期", value: 20 },
];

// 顶部 8 张指标卡
const SUMMARY_METRICS: { key: string; label: string; tip: string }[] = [
  { key: "roe", label: "ROE", tip: "净资产收益率" },
  { key: "gross_margin", label: "毛利率", tip: "销售毛利率" },
  { key: "net_margin", label: "净利率", tip: "销售净利率" },
  { key: "debt_to_asset", label: "资产负债率", tip: "总负债 / 总资产" },
  { key: "current_ratio", label: "流动比率", tip: "流动资产 / 流动负债" },
  { key: "ocf_to_net_profit", label: "经营现金流/净利润", tip: "经营性现金流净额 / 净利润" },
  { key: "revenue_yoy", label: "营收同比", tip: "营业收入同比增长" },
  { key: "profit_yoy", label: "净利润同比", tip: "净利润同比增长" },
];

// 历史曲线统一用百分比口径的指标，保证同一 Y 轴可读
const CHART_METRIC_KEYS = [
  "roe",
  "gross_margin",
  "net_margin",
  "debt_to_asset",
  "revenue_yoy",
  "profit_yoy",
];

const METRIC_LABELS: Record<string, string> = {
  roe: "ROE",
  gross_margin: "毛利率",
  net_margin: "净利率",
  debt_to_asset: "资产负债率",
  revenue_yoy: "营收同比",
  profit_yoy: "净利润同比",
};

function formatValue(value: number | null | undefined, unit?: string | null): string {
  if (value == null || Number.isNaN(value)) return "--";
  const dec = Math.abs(value) >= 100 ? 1 : 2;
  const str = value.toLocaleString("zh-CN", { maximumFractionDigits: dec });
  return unit ? `${str}${unit}` : str;
}

function formatYi(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return "--";
  return `${(value / 1e8).toFixed(2)}亿`;
}

function QualityTag({
  quality,
  source,
}: {
  quality?: string | null;
  source?: string | null;
}) {
  const preset =
    quality === "derived"
      ? { color: "#faad14", label: "估算" }
      : quality === "reported"
        ? { color: "#52c41a", label: "财报" }
        : null;
  return (
    <Tooltip title={`质量: ${quality ?? "未知"}${source ? ` · 来源: ${source}` : ""}`}>
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 4,
          fontSize: 12,
          color: "#999",
        }}
      >
        {preset ? (
          <span
            style={{
              width: 8,
              height: 8,
              borderRadius: "50%",
              background: preset.color,
              display: "inline-block",
            }}
          />
        ) : null}
        {preset?.label}
        {source ? ` · ${source}` : ""}
      </span>
    </Tooltip>
  );
}

function SummaryMetricCards({ summary }: { summary?: FinancialSummary }) {
  if (!summary) return null;
  return (
    <Card size="small" title="财务摘要">
      <Row gutter={[16, 16]}>
        {SUMMARY_METRICS.map((m) => {
          const metric = summary.metrics?.[m.key];
          return (
            <Col key={m.key} xs={12} sm={8} lg={6}>
              <Tooltip title={m.tip}>
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>
                  {m.label}
                </Typography.Text>
              </Tooltip>
              <div style={{ fontSize: 20, fontWeight: 600, marginTop: 4 }}>
                {formatValue(metric?.value, metric?.unit)}
              </div>
              <div style={{ marginTop: 4 }}>
                <QualityTag quality={metric?.quality} source={metric?.source} />
              </div>
            </Col>
          );
        })}
      </Row>
    </Card>
  );
}

function useSummary(exchange: Exchange, symbol: string) {
  return useQuery({
    queryKey: ["financial-summary", exchange, symbol],
    queryFn: () => fetchFinancialSummary(exchange, symbol),
    enabled: Boolean(exchange && symbol),
  });
}

function MetricsHistoryChart({ histories }: { histories: MetricHistory[] }) {
  const periods = useMemo(() => histories[0]?.points.map((p) => p.period) ?? [], [histories]);
  const option = useMemo(
    () => ({
      tooltip: { trigger: "axis" as const },
      legend: { data: histories.map((h) => METRIC_LABELS[h.metric_key] ?? h.metric_key) },
      grid: { left: 60, right: 20, top: 40, bottom: 40 },
      xAxis: { type: "category" as const, data: periods },
      yAxis: { type: "value" as const, scale: true },
      series: histories.map((h) => ({
        name: METRIC_LABELS[h.metric_key] ?? h.metric_key,
        type: "line" as const,
        smooth: true,
        showSymbol: false,
        data: h.points.map((p) => p.value),
      })),
    }),
    [histories, periods]
  );
  return <ReactECharts option={option} style={{ height: 320 }} />;
}

// ---------------------------------------------------------------------------
// 三大报表表格
// ---------------------------------------------------------------------------

interface StatementField {
  key: string;
  label: string;
  money?: boolean;
}

const INCOME_FIELDS: StatementField[] = [
  { key: "revenue", label: "营业收入", money: true },
  { key: "operate_cost", label: "营业成本", money: true },
  { key: "operate_profit", label: "营业利润", money: true },
  { key: "total_profit", label: "利润总额", money: true },
  { key: "n_income", label: "净利润", money: true },
  { key: "n_income_attr_p", label: "归母净利润", money: true },
  { key: "deduct_n_income", label: "扣非净利润", money: true },
  { key: "sell_exp", label: "销售费用", money: true },
  { key: "admin_exp", label: "管理费用", money: true },
  { key: "fin_exp", label: "财务费用", money: true },
  { key: "rd_exp", label: "研发费用", money: true },
  { key: "basic_eps", label: "基本每股收益(元)" },
  { key: "diluted_eps", label: "稀释每股收益(元)" },
];

const BALANCE_FIELDS: StatementField[] = [
  { key: "total_assets", label: "总资产", money: true },
  { key: "total_liab", label: "总负债", money: true },
  { key: "total_hldr_eqy_exc_min_int", label: "归母净资产", money: true },
  { key: "total_hldr_eqy_inc_min_int", label: "净资产(含少数股东)", money: true },
  { key: "money_cap", label: "货币资金", money: true },
  { key: "accounts_receiv", label: "应收账款", money: true },
  { key: "inventories", label: "存货", money: true },
  { key: "fix_assets", label: "固定资产", money: true },
  { key: "intan_assets", label: "无形资产", money: true },
  { key: "st_borrow", label: "短期借款", money: true },
  { key: "lt_borrow", label: "长期借款", money: true },
];

const CASH_FLOW_FIELDS: StatementField[] = [
  { key: "n_cashflow_act", label: "经营活动现金流净额", money: true },
  { key: "n_cashflow_inv_act", label: "投资活动现金流净额", money: true },
  { key: "n_cashflow_fin_act", label: "筹资活动现金流净额", money: true },
  { key: "c_cash_equ_end_period", label: "期末现金及等价物", money: true },
];

function buildStatementTable(
  rows: Record<string, unknown>[],
  periods: string[],
  fields: StatementField[]
) {
  const columns = [
    { title: "科目", dataIndex: "label", key: "label", width: 180, fixed: "left" as const },
    ...periods.map((p) => ({
      title: p,
      key: p,
      dataIndex: p,
      align: "right" as const,
      width: 110,
    })),
  ];
  const data = fields.map((f) => {
    const row: Record<string, string> = { key: f.label, label: f.label };
    for (const p of periods) {
      const r = rows.find((it) => it["period"] === p);
      const raw = r?.[f.key] as number | null | undefined;
      row[p] = f.money ? formatYi(raw) : formatValue(raw);
    }
    return row;
  });
  return (
    <Table
      size="small"
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ x: "max-content" }}
    />
  );
}

function StatementsCard({
  title,
  rows,
  periods,
  fields,
}: {
  title: string;
  rows: Record<string, unknown>[];
  periods: string[];
  fields: StatementField[];
}) {
  return (
    <Card
      size="small"
      title={title}
      extra={<Typography.Text type="secondary" style={{ fontSize: 12 }}>金额单位：亿元</Typography.Text>}
    >
      {rows.length && periods.length ? (
        buildStatementTable(rows, periods, fields)
      ) : (
        <Empty description="暂无数据" />
      )}
    </Card>
  );
}

export function FinancialTab({ exchange, symbol }: Props) {
  const [periodCount, setPeriodCount] = useState<number>(8);

  const summaryQuery = useSummary(exchange, symbol);
  const historyQuery = useQuery({
    queryKey: ["financial-metrics-history", exchange, symbol],
    queryFn: () => fetchFinancialMetricsHistory(exchange, symbol, CHART_METRIC_KEYS),
    enabled: Boolean(exchange && symbol),
  });
  const statementsQuery = useQuery({
    queryKey: ["financial-statements", exchange, symbol, periodCount],
    queryFn: () => fetchFinancialStatements(exchange, symbol, periodCount),
    enabled: Boolean(exchange && symbol),
  });

  const histories = historyQuery.data ?? [];
  const statements = statementsQuery.data;
  const periods = useMemo(
    () => statements?.periods?.map((p) => p.period) ?? [],
    [statements]
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {summaryQuery.isLoading ? (
        <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
          <Spin />
        </div>
      ) : (
        <SummaryMetricCards summary={summaryQuery.data} />
      )}

      <Card
        size="small"
        title="财务指标趋势"
        extra={
          <Segmented
            size="small"
            options={PERIOD_COUNTS}
            value={periodCount}
            onChange={(v) => setPeriodCount(v as number)}
          />
        }
      >
        {historyQuery.isLoading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : histories.length ? (
          <MetricsHistoryChart histories={histories} />
        ) : (
          <Empty description="暂无财务指标历史数据" />
        )}
      </Card>

      <Card size="small" title="财务报告">
        {statementsQuery.isLoading ? (
          <div style={{ display: "flex", justifyContent: "center", padding: 40 }}>
            <Spin />
          </div>
        ) : !statements ? (
          <Empty description="暂无财务报告数据" />
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              style={{ marginBottom: 12 }}
              message={`报告期共 ${periods.length} 期 · 金额单位为人民币亿元`}
            />
            <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <StatementsCard
                title="利润表"
                rows={statements.income_statement as unknown as Record<string, unknown>[]}
                periods={periods}
                fields={INCOME_FIELDS}
              />
              <StatementsCard
                title="资产负债表"
                rows={statements.balance_sheet as unknown as Record<string, unknown>[]}
                periods={periods}
                fields={BALANCE_FIELDS}
              />
              <StatementsCard
                title="现金流量表"
                rows={statements.cash_flow as unknown as Record<string, unknown>[]}
                periods={periods}
                fields={CASH_FLOW_FIELDS}
              />
            </div>
          </>
        )}
      </Card>
    </div>
  );
}
