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

/**
 * 三张成长/盈利卡读的 `financial-summary.metrics` 键。
 *
 * **写成字符串字面量联合而非 `string`**：本卡修的就是"读了一个后端不存在的字段名"
 * （`stock.roe` / `revenueGrowth` / `profitGrowth`），宽化到 `string` 后 `metrics?.[key]`
 * 会被 TS 当成任意索引、拼错键名照样通过类型检查。收窄后，错键名 = 编译错误。
 */
type GrowthMetricKey = "roe" | "revenue_yoy" | "profit_yoy";

interface GrowthMetricDef {
  /** `financial-summary` 的 metrics 键（三键与后端 `FinancialSummaryOut` 一致） */
  key: GrowthMetricKey;
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
  // 三格的 metric 先取出来（卡片标题右侧要判断"是否至少有一个值"，见下）
  const growth = GROWTH_METRICS.map((m) => {
    const metric = summary?.metrics?.[m.key];
    const value = metric?.value;
    return {
      def: m,
      metric,
      value,
      // 有值 = 非 null 且非 NaN。`0` 是**合法值**（不能写成 `value ?` 这种 falsy 判断）
      hasValue: value != null && !Number.isNaN(value),
    };
  });
  // 报告期是**卡片级**信息（同一份财报覆盖三格），只在至少一格有值、且后端给了
  // latest_period 时显示一次；逐格重复三遍没有信息增量，还会把每格拉高两行。
  const showPeriod = Boolean(summary?.latest_period) && growth.some((g) => g.hasValue);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <Card title="估值指标" size="small">
        <Row gutter={[16, 16]}>
          {VALUATION_METRICS.map((m) => (
            // `lg={12}`：`lg` 起本卡挪进 `lg={8}` 的侧栏（约 1/3 页宽），四列会把
            // 「1.66万亿」折成两行（P1-3 实测）。侧栏里改成 2×2；中屏/窄屏本卡仍全宽，维持四列。
            <Col key={m.key} xs={12} sm={6} lg={12}>
              <Statistic
                title={<MetricLabel label={m.label} tip={m.tip} />}
                valueRender={() => renderValuation(stock, m)}
              />
            </Col>
          ))}
        </Row>
      </Card>
      <Card
        title="成长与盈利"
        size="small"
        extra={
          showPeriod ? (
            <span style={{ fontSize: 12, color: colors.textSecondary }}>
              报告期 {summary?.latest_period}
            </span>
          ) : undefined
        }
      >
        <Row gutter={[16, 16]}>
          {growth.map(({ def, metric, value, hasValue }) => (
            <Col key={def.key} xs={12} sm={8}>
              <Statistic
                title={<MetricLabel label={def.label} tip={def.tip} />}
                valueRender={() => <GrowthValue value={value} signed={def.signed} />}
              />
              {/* 质量徽标是**逐指标**的（三键的 quality/source 可以不同），故留在格内；
                  无值时不渲染——`--` 旁边挂一个"财报"徽标是自相矛盾的 */}
              {hasValue && (
                <div style={{ marginTop: 2 }}>
                  <QualityTag quality={metric?.quality} source={metric?.source} />
                </div>
              )}
            </Col>
          ))}
        </Row>
      </Card>
    </div>
  );
}
