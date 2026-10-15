import type { MetricLatest } from "@/shared/api/industryResearch";
import type { SentimentPercentileField, SentimentTrendPoint, SentimentTrends } from "@/shared/api/limitUp";
import { formatCnDate } from "@/shared/ui";
import { formatMetricValue, formatQuantile, FREQ_LABEL } from "../metricFormat";
import type { SentimentCaliberNote, SentimentModule, SentimentPercentileRow, SentimentRow } from "../moduleTable";

/**
 * 模块面板的**唯一渲染器**（`moduleTable.ts` 是唯一模块表）。
 *
 * 它不知道"市场宽度"或"资金流向"是什么——它只认三种行：
 *  - `metric`     —— 值来自 `/api/v1/metrics/latest`，可带一条后端已提供的分位 chip；
 *  - `percentile` —— 只有分位（该值不在 registry 里），值取分位窗口的末点；
 *  - `unbuilt`    —— 模块未落地的结论 + 出处（就地说明，不留空图）。
 *
 * 三条硬约束（与 `SentimentThermometer` 的历史分位同族）：
 *  1. 缺失一律 `--`，**绝不** `?? 0`；
 *  2. 分位只显示后端 `percentile` 里真有的字段，且**每个 chip 自带样本窗口**；
 *  3. `insufficientHistory` / `percentile=null` ⇒ 只显示「样本不足」，不给分位数字。
 */

/** 一个分位 chip：自带窗口的量化梯（与温度计的历史分位 chip 同一套读法）。 */
function PercentileChip({
  field,
  trends,
}: {
  field: SentimentPercentileField;
  trends?: SentimentTrends | null;
}) {
  if (!trends) {
    return (
      <span className="sent-pct sent-pct--none num" data-testid={`sentiment-pct-unavailable-${field}`}>
        历史分位未取到（/market/sentiment/trends 未就绪）
      </span>
    );
  }
  const window = trends.window;
  if (trends.insufficientHistory || !trends.percentile) {
    return (
      <span className="sent-pct sent-pct--none num" data-testid={`sentiment-pct-insufficient-${field}`}>
        样本不足（近 {window.samples} 个交易日 · 不足 60 个有效样本不给分位）
      </span>
    );
  }
  const p = trends.percentile[field];
  if (!p) {
    return (
      <span className="sent-pct sent-pct--none num" data-testid={`sentiment-pct-${field}`}>
        无有效样本
      </span>
    );
  }
  return (
    <span className="sent-pct num" data-testid={`sentiment-pct-${field}`}>
      <span className="sent-pct__q">
        P25 {formatQuantile(p.p25, field)} / P50 {formatQuantile(p.p50, field)} / P75{" "}
        {formatQuantile(p.p75, field)}
      </span>
      <span className="sent-pct__window">
        近 {p.sampleCount} 交易日分位（{p.from ?? "--"} 至 {p.to ?? "--"}）
      </span>
    </span>
  );
}

/** 分位窗口末点的值（`percentile` 行的当期值；缺失给 `--`）。 */
function pointValue(point: SentimentTrendPoint | null, field: SentimentPercentileField): string {
  if (!point) return "--";
  if (field === "zt_count") return formatMetricValue(point.ztCount, "家");
  if (field === "broken_rate") {
    return point.brokenRate == null ? "--" : `${(point.brokenRate * 100).toFixed(2)}%`;
  }
  return point.promo1to2 == null ? "--" : `${(point.promo1to2 * 100).toFixed(2)}%`;
}

/** 值单元格右下角的口径注（渲染在所属行正下方，占满整行）。 */
function CaliberNoteRow({ note }: { note: SentimentCaliberNote }) {
  return (
    <tr className="sent-caliber-row">
      <td colSpan={3} data-testid={`sentiment-caliber-${note.id}`}>
        <span className="sent-caliber__src">{note.source}</span>
        <span className="sent-caliber__text">{note.text}</span>
      </td>
    </tr>
  );
}

function MetricRowView({
  row,
  metric,
  metricsPending,
  trends,
}: {
  row: Extract<SentimentRow, { kind: "metric" }>;
  metric: MetricLatest | undefined;
  metricsPending: boolean;
  trends?: SentimentTrends | null;
}) {
  const value = metric?.value ?? null;
  return (
    <>
      <tr className="sent-row" data-testid={`sentiment-metric-${row.metricKey}`}>
        <td className="sent-cell-label">{metric?.name ?? row.label}</td>
        <td className="sent-cell-value num">
          <span className="sent-value num">{formatMetricValue(value, metric?.unit)}</span>
          {value == null ? (
            <span className="sent-missing" data-testid={`sentiment-missing-${row.metricKey}`}>
              {metricsPending
                ? "读取中…"
                : metric === undefined
                  ? "该 metric_key 未在 /metrics/latest 返回"
                  : `${metric.missingReason ?? "missing_reason 未登记"}${
                      metric.coverage == null ? "" : ` · 覆盖率 ${metric.coverage}`
                    }`}
            </span>
          ) : null}
          {row.percentileField ? <PercentileChip field={row.percentileField} trends={trends} /> : null}
        </td>
        <td className="sent-cell-meta num">
          {/* 一行写完口径与 vintage（避免每个指标重复三行把读出台拉成电话簿） */}
          <span className="sent-meta__line">
            {[
              metric?.asOf ? `数据截至 ${formatCnDate(metric.asOf)}` : "无口径日",
              metric?.freq ? FREQ_LABEL[metric.freq] ?? metric.freq : null,
              metric?.caliberVersion ? `口径 ${metric.caliberVersion}` : null,
            ]
              .filter(Boolean)
              .join(" · ")}
          </span>
          {metric?.asOfSource === "period" ? (
            <span className="sent-flag">PIT 缺发布时刻，按 period</span>
          ) : null}
        </td>
      </tr>
      {row.caliber ? <CaliberNoteRow note={row.caliber} /> : null}
    </>
  );
}

function PercentileRowView({ row, trends }: { row: SentimentPercentileRow; trends?: SentimentTrends | null }) {
  const series = trends?.series ?? [];
  const last = series.length > 0 ? series[series.length - 1] : null;
  return (
    <tr className="sent-row" data-testid={`sentiment-percentile-row-${row.field}`}>
      <td className="sent-cell-label">
        {row.label}
        <span className="sent-tag">仅分位</span>
      </td>
      <td className="sent-cell-value num">
        <span className="sent-value num">{pointValue(last, row.field)}</span>
        {last ? <span className="sent-missing">{last.tradeDate}</span> : null}
        <PercentileChip field={row.field} trends={trends} />
      </td>
      <td className="sent-cell-meta num">
        <span>{row.note}</span>
      </td>
    </tr>
  );
}

function UnbuiltNotice({ module, row }: { module: SentimentModule; row: Extract<SentimentRow, { kind: "unbuilt" }> }) {
  return (
    <div className="sent-unbuilt" data-testid={`sentiment-unbuilt-${module.key}`}>
      <p className="sent-unbuilt__head">
        <span className="sent-unbuilt__tag">{row.ruling}</span>
        {row.headline}
      </p>
      <p className="sent-unbuilt__detail">{row.detail}</p>
    </div>
  );
}

export interface SentimentModulePanelProps {
  module: SentimentModule;
  /** metric_key → 通用读面的最新值（含 missing_reason / coverage） */
  metrics: Map<string, MetricLatest>;
  /** 指标读面尚在请求中：此时缺失文案是「读取中…」而不是「未返回」 */
  metricsPending?: boolean;
  trends?: SentimentTrends | null;
}

/** 一个模块 = 模块头 + 一张表（或一条未落地结论）。 */
export function SentimentModulePanel({ module, metrics, metricsPending = false, trends }: SentimentModulePanelProps) {
  const tableRows = module.rows.filter((r): r is Exclude<SentimentRow, { kind: "unbuilt" }> => r.kind !== "unbuilt");
  const unbuilt = module.rows.filter(
    (r): r is Extract<SentimentRow, { kind: "unbuilt" }> => r.kind === "unbuilt",
  );
  return (
    <section
      className="sent-module"
      id={`sentiment-module-${module.key}`}
      data-testid={`sentiment-module-${module.key}`}
    >
      <header className="sent-module__head">
        <h3 className="sent-module__title">{module.title}</h3>
        <p className="sent-module__question">{module.question}</p>
      </header>
      {module.note ? <p className="sent-module__note">{module.note}</p> : null}
      {tableRows.length > 0 ? (
        <table className="sent-table">
          <thead>
            <tr>
              <th scope="col">指标</th>
              <th scope="col" className="sent-th--num">
                数值
              </th>
              <th scope="col">窗口 / 新鲜度</th>
            </tr>
          </thead>
          <tbody>
            {tableRows.map((row) =>
              row.kind === "metric" ? (
                <MetricRowView
                  key={row.metricKey}
                  row={row}
                  metric={metrics.get(row.metricKey)}
                  metricsPending={metricsPending}
                  trends={trends}
                />
              ) : (
                <PercentileRowView key={row.field} row={row} trends={trends} />
              ),
            )}
          </tbody>
        </table>
      ) : null}
      {unbuilt.map((row) => (
        <UnbuiltNotice key={row.ruling} module={module} row={row} />
      ))}
    </section>
  );
}
