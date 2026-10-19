import { Alert, Button, Card, Row, Col, Statistic, Tooltip, Typography } from "antd";
import { InfoCircleOutlined } from "@ant-design/icons";
import { useState } from "react";
import { BlockBoundary, NumberText, ChangeText } from "@/shared/ui";
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
   * 加载中 / 无财报数据时都是 `undefined` ⇒ 三格显示 `--`（**不阻塞**整页渲染：
   * 估值卡与 K 线不依赖它）。但这两种情形**不是同一件事**：加载中不出任何"无记录"文案，
   * 只有成功且三格全空才写「暂无财报数据」；取数失败则是行内「加载失败 + 重试」
   * （三态见 `GrowthCard`，失败 ≠ 无记录）。
   */
  summary?: FinancialSummary;
  /** 财务摘要查询是否仍在**首屏加载**（React Query 的 `isLoading`，不是 `isFetching`） */
  summaryLoading?: boolean;
  /** 财务摘要查询失败（500/断网），**不是**"没有财报数据" */
  summaryError?: boolean;
  /** 重试 = `summaryQuery.refetch()`；仅错误态的「重试」调用它 */
  onRetrySummary?: () => void | Promise<unknown>;
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

/**
 * 两张卡在 1440×900 侧栏（`lg={8}`）下的**实测自然高度**，作为 `BlockBoundary` 的占位高度。
 *
 * 数字来源：P2-1 用 Playwright `boundingBox().height` 量的真实渲染高度，不是"看着差不多"：
 * - 估值指标 208 = 标题行 + 2×2 四格（只读个股名录，落地即稳定）；
 * - 成长与盈利 152 = 标题行 + 三格 + **质量徽标行**。加载中三格只有 `--`（无徽标）时是 128px，
 *   数据落地后每格多一行徽标 ⇒ 152px。差的这 24px 就是改造前"数据一到、下面全往下掉"的来源；
 *   占位高度补上它之后，加载/成功/失败三种状态都占 152px。
 */
const VALUATION_CARD_MIN_HEIGHT = 208;
const GROWTH_CARD_MIN_HEIGHT = 152;

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

/** 估值指标卡：只读个股名录（无查询）⇒ 落地即最终形态，不需要骨架。 */
function ValuationCard({ stock }: { stock: StockRecord }) {
  return (
    <Card title="估值指标" size="small" data-testid="valuation-card">
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
  );
}

/**
 * 成长与盈利卡：**三态显式分开**（本任务的核心，别再退回"一律 `--`"）。
 *
 * - **加载中** → 三格 `--`（骨架），**不写**「暂无…」：还没到 ≠ 没有；
 * - **成功且三格全空** → `--` + 一行「暂无财报数据」：这才是"无记录"；
 * - **失败** → 行内 `Alert`（「加载失败」+「重试」），**绝不**出现「暂无财报数据」——
 *   请求失败被写成"没有数据"正是本任务要消灭的那类谎言；
 * - 重试在途时**保留**错误块：React Query v5 的 refetch 一开始就把 `error` 清成 `null`、
 *   `status` 回落 `pending`，只看 `isError` 的话点「重试」会闪成一张 `--` 卡，看起来像
 *   "重试后变成没有数据"。故用本地 `retrying` 撑住错误态，并让按钮在途时真禁用。
 */
function GrowthCard({
  summary,
  loading,
  error,
  onRetry,
}: {
  summary?: FinancialSummary;
  loading: boolean;
  error: boolean;
  onRetry?: () => void | Promise<unknown>;
}) {
  const { colors } = useTheme();
  const [retrying, setRetrying] = useState(false);

  const handleRetry = async () => {
    if (!onRetry) return;
    setRetrying(true);
    try {
      await onRetry();
    } finally {
      setRetrying(false);
    }
  };

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
  const anyValue = growth.some((g) => g.hasValue);
  // 报告期是**卡片级**信息（同一份财报覆盖三格），只在至少一格有值、且后端给了
  // latest_period 时显示一次；逐格重复三遍没有信息增量，还会把每格拉高两行。
  const showPeriod = Boolean(summary?.latest_period) && anyValue;

  // 失败态优先于空态：`error` 在 refetch 在途时会被查询层清掉，故用 `retrying` 兜住
  const failed = error || retrying;
  // "无记录"只在**成功且确实没有值**时成立（loading / error 一律不算）
  const empty = !failed && !loading && !anyValue;

  return (
    <Card
      title="成长与盈利"
      size="small"
      data-testid="growth-card"
      // 固定高度占位（实测自然高度 152）：加载中三格只有 `--`（128px），数据落地后
      // 每格多一行质量徽标（152px）。不钉住这 24px，下面三块侧栏卡会在数据到达时整体下移。
      style={{ minHeight: GROWTH_CARD_MIN_HEIGHT }}
      extra={
        showPeriod ? (
          <span style={{ fontSize: 12, color: colors.textSecondary }}>
            报告期 {summary?.latest_period}
          </span>
        ) : undefined
      }
    >
      {failed ? (
        <Alert
          type="error"
          showIcon
          data-testid="growth-card-error"
          // 文案压成**单行**：错误卡的天然高度若超过本卡占位高度（152px），
          // 失败态会把下面的侧栏卡往下推 —— 失败也不该搬家，故不挂第二行 description。
          message="加载失败 · 不代表没有财报数据"
          action={
            <Button
              size="small"
              loading={retrying}
              disabled={retrying}
              data-testid="growth-card-retry"
              onClick={() => void handleRetry()}
            >
              重试
            </Button>
          }
        />
      ) : (
        <>
          {empty && (
            <Typography.Text
              type="secondary"
              style={{ fontSize: 12, display: "block", marginBottom: 8 }}
              data-testid="growth-card-empty"
            >
              暂无财报数据
            </Typography.Text>
          )}
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
        </>
      )}
    </Card>
  );
}

/**
 * 侧栏上方的两张卡。**每张卡各自一层 `BlockBoundary`**（不是整块一层）：
 * 估值指标只读名录、成长与盈利读 `financial-summary`，两者没有任何共享状态 ——
 * 一张卡渲染崩了不该把另一张也换掉（blast radius 越小越好）。
 */
export function FundamentalCards({
  stock,
  summary,
  summaryLoading = false,
  summaryError = false,
  onRetrySummary,
}: Props) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <BlockBoundary title="估值指标" minHeight={VALUATION_CARD_MIN_HEIGHT}>
        <ValuationCard stock={stock} />
      </BlockBoundary>
      <BlockBoundary title="成长与盈利" minHeight={GROWTH_CARD_MIN_HEIGHT}>
        <GrowthCard
          summary={summary}
          loading={summaryLoading}
          error={summaryError}
          onRetry={onRetrySummary}
        />
      </BlockBoundary>
    </div>
  );
}
