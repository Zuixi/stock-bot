import { Card, Row, Col, Statistic, Tooltip, Typography } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { NumberText, ChangeText } from "@/shared/ui";
import type { StockRecord } from "@/shared/types";

interface Props {
  stock: StockRecord;
}

interface MetricDef {
  label: string;
  key: keyof StockRecord;
  type: "cap" | "number" | "percent";
  tip?: string;
}

const VALUATION_METRICS: MetricDef[] = [
  { label: "总市值", key: "marketCap", type: "cap", tip: "总股本 × 最新收盘价" },
  { label: "流通市值", key: "circulatingCap", type: "cap", tip: "流通股本 × 最新收盘价" },
  { label: "PE(TTM)", key: "pe", type: "number", tip: "市盈率（滚动12个月）" },
  { label: "PB", key: "pb", type: "number", tip: "市净率 = 总市值 / 净资产" },
];

const GROWTH_METRICS: MetricDef[] = [
  { label: "ROE", key: "roe", type: "percent", tip: "净资产收益率" },
  { label: "营收同比", key: "revenueGrowth", type: "percent", tip: "最近报告期营收同比增长" },
  { label: "净利润同比", key: "profitGrowth", type: "percent", tip: "最近报告期净利润同比增长" },
];

function MetricLabel({ label, tip }: { label: string; tip?: string }) {
  return (
    <span>
      {label}
      {tip && (
        <Tooltip title={tip}>
          <InfoCircleOutlined style={{ marginLeft: 4, color: "#999", fontSize: 12 }} />
        </Tooltip>
      )}
    </span>
  );
}

function renderValue(stock: StockRecord, m: MetricDef) {
  const val = stock[m.key] as number | undefined;
  if (m.type === "cap") return <NumberText value={val} unit="cap" />;
  if (m.type === "percent") return <ChangeText value={val} />;
  return <NumberText value={val} />;
}

export function FundamentalCards({ stock }: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title="估值指标" size="small">
        <Row gutter={[16, 16]}>
          {VALUATION_METRICS.map((m) => (
            <Col key={m.key} xs={12} sm={6}>
              <Statistic
                title={<MetricLabel label={m.label} tip={m.tip} />}
                valueRender={() => renderValue(stock, m)}
              />
            </Col>
          ))}
        </Row>
      </Card>
      <Card title="成长与盈利" size="small">
        <Row gutter={[16, 16]}>
          {GROWTH_METRICS.map((m) => (
            <Col key={m.key} xs={12} sm={8}>
              <Statistic
                title={<MetricLabel label={m.label} tip={m.tip} />}
                valueRender={() => renderValue(stock, m)}
              />
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
