import { Card, Row, Col, Statistic, Tooltip } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { NumberText, ChangeText } from "@/shared/ui";
import { useTheme } from "@/app/theme-context";
import type { StockRecord } from "@/shared/types";
import type { FinancialSummary } from "@/shared/api/financial";
import { QualityTag } from "./QualityTag";

interface Props {
  stock: StockRecord;
  /**
   * 财务摘要（`GET /financial-summary`）。概览页与财务 tab 用**同一个** query key，
   * 所以切换到财务 tab 时不会再请求一次。
   *
   * 加载中 / 无财报数据时都是 `undefined` ⇒ 三张卡显示 `--`（**不阻塞**整页渲染：
   * 估值卡与 K 线不依赖它）。
   */
  summary?: FinancialSummary;
}

interface ValuationMetricDef {
  label: string;
  key: keyof StockRecord;
  type: "cap" | "number" | "percent";
  tip?: string;
}

const VALUATION_METRICS: ValuationMetricDef[] = [
  { label: "总市值", key: "marketCap", type: "cap", tip: "总股本 × 最新收盘价" },
  { label: "流通市值", key: "circulatingCap", type: "cap", tip: "流通股本 × 最新收盘价" },
  { label: "PE(TTM)", key: "pe", type: "number", tip: "市盈率（滚动12个月）" },
  { label: "PB", key: "pb", type: "number", tip: "市净率 = 总市值 / 净资产" },
];

interface GrowthMetricDef {
  /** `financial-summary` 的 metrics 键，如 `roe` / `revenue_yoy` / `profit_yoy` */
  key: string;
  label: string;
  tip?: string;
  /**
   * 是否按「变化」渲染（带 `+`/`-` 符号并随正负上色）。
   *
   * ROE 是**存量比率**（报告期口径的净资产收益率，单位 %），不是"相对于上一期的变化"。
   * 用 `ChangeText` 会渲染成 `+17.95%` 并套上"涨"色 —— 在个股详情页里红色 `+17.95%`
   * 会被直接读成当日涨幅（同页 K 线/头部的涨跌就是这套配色），语义错位。
   * 故 ROE 走不带符号的 `17.95%`，用主文本色；两个同比指标才是变化量，走 `ChangeText`。
   */
  signed: boolean;
}

const GROWTH_METRICS: GrowthMetricDef[] = [
  { key: "roe", label: "ROE", tip: "净资产收益率（最新报告期，未年化）", signed: false },
  { key: "revenue_yoy", label: "营收同比", tip: "最近报告期营收同比增长", signed: true },
  { key: "profit_yoy", label: "净利润同比", tip: "最近报告期净利润同比增长", signed: true },
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

function renderValuation(stock: StockRecord, m: ValuationMetricDef) {
  const val = stock[m.key] as number | undefined;
  if (m.type === "cap") return <NumberText value={val} unit="cap" />;
  if (m.type === "percent") return <ChangeText value={val} />;
  return <NumberText value={val} />;
}

/**
 * 成长/盈利指标的数值渲染。
 *
 * 口径（与后端 `financial-summary` 一致）：`value` 已是以 `%` 为单位的数值（如 `12.34`
 * 表示 12.34%），**不为 null 时才能渲染**——缺失即 `--`，绝不当 0（缺失 ≠ 0）。
 */
function GrowthValue({ value, signed }: { value?: number | null; signed: boolean }) {
  const { colors } = useTheme();
  if (value == null || Number.isNaN(value)) {
    return <span className="num" style={{ color: colors.flat }}>--</span>;
  }
  // 同比变化：统一走既有 ChangeText（`+12.34%` / `-5.00%`，按正负上色）
  if (signed) return <ChangeText value={value} />;
  // 存量比率（ROE）：不带符号、不上涨跌色，避免被读成当日涨跌幅
  return (
    <span className="num" style={{ color: colors.textPrimary }}>
      {value.toFixed(2)}%
    </span>
  );
}

export function FundamentalCards({ stock, summary }: Props) {
  const { colors } = useTheme();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title="估值指标" size="small">
        <Row gutter={[16, 16]}>
          {VALUATION_METRICS.map((m) => (
            <Col key={m.key} xs={12} sm={6}>
              <Statistic
                title={<MetricLabel label={m.label} tip={m.tip} />}
                valueRender={() => renderValuation(stock, m)}
              />
            </Col>
          ))}
        </Row>
      </Card>
      <Card title="成长与盈利" size="small">
        <Row gutter={[16, 16]}>
          {GROWTH_METRICS.map((m) => {
            const metric = summary?.metrics?.[m.key];
            const value = metric?.value;
            // 报告期与质量徽标只在**有值**时出现：`--` 旁边挂一个"财报"徽标是自相矛盾的
            const hasValue = value != null && !Number.isNaN(value);
            return (
              <Col key={m.key} xs={12} sm={8}>
                <Statistic
                  title={<MetricLabel label={m.label} tip={m.tip} />}
                  valueRender={() => <GrowthValue value={value} signed={m.signed} />}
                />
                {hasValue && (
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 6,
                      marginTop: 2,
                      fontSize: 12,
                      color: colors.textSecondary,
                    }}
                  >
                    {summary?.latest_period ? <span>{summary.latest_period}</span> : null}
                    <QualityTag quality={metric?.quality} source={metric?.source} />
                  </div>
                )}
              </Col>
            );
          })}
        </Row>
      </Card>
    </div>
  );
}
