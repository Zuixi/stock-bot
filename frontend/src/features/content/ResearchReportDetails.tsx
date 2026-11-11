import { Tag } from "antd";
import type { ContentItem, ContentMetric } from "@/shared/api/content";
import { NumberText } from "@/shared/ui";

/**
 * 研报行（`content_type='report'`）的结构化元数据。
 *
 * 口径（S3.4 / S3.7）：
 * - 只展示源站**真实给出**的字段；缺失一律 `—`，不补 `0`；
 * - `core_view` 在 Plan 2 未接 LLM，显式显示"未抽取"，不编造观点；
 * - 被自动判据过滤的研报（默认不返回）带 `ineffective_reasons`，打开开关后
 *   在行内显示原因码的中文含义，未知码原样上屏（不吞后端新增原因）。
 */

const REASON_LABEL: Record<string, string> = {
  superseded: "重复（已被更新的同评级报告取代）",
  no_quantitative: "无量化字段（评级 / 目标价 / EPS / PE）",
  industry_mismatch: "行业不相关",
  human_rejected: "人工已否决",
};

function textOf(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function metricOf(item: ContentItem, key: string): ContentMetric | undefined {
  return item.metrics.find((m) => m.metric_key === key);
}

function forecastRows(item: ContentItem, key: string): ContentMetric[] {
  return item.metrics
    .filter((m) => m.metric_key === key && m.period)
    .sort((a, b) => (a.period ?? "").localeCompare(b.period ?? ""));
}

function ReportMetric({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="content-list__metric">
      <dt className="content-list__metric-label">{label}</dt>
      <dd className="content-list__metric-value">{children}</dd>
    </div>
  );
}

export function ResearchReportDetails({ item }: { item: ContentItem }) {
  const rating = metricOf(item, "rating");
  const ratingChange = metricOf(item, "rating_change");
  const targetPrice = metricOf(item, "target_price");
  const epsRows = forecastRows(item, "eps_forecast");
  const peRows = forecastRows(item, "pe_forecast");
  const industry = textOf(item.extracted?.industry);
  const coreView = textOf(item.extracted?.core_view);
  const coreViewStatus = textOf(item.extracted?.core_view_status);
  const reasons = item.effective === false ? item.ineffective_reasons : [];

  return (
    <>
      {reasons.length > 0 ? (
        <p className="content-list__report-reasons" data-testid="research-ineffective-reasons">
          <Tag color="warning" bordered={false}>
            被过滤
          </Tag>
          {reasons.map((code) => REASON_LABEL[code] ?? code).join("；")}
        </p>
      ) : null}

      <dl className="content-list__metrics content-list__metrics--report">
        <ReportMetric label="评级">
          {rating?.value_text ? (
            rating.value_text
          ) : (
            <span className="content-list__metric-num">—</span>
          )}
        </ReportMetric>

        <ReportMetric label="评级变动">
          {ratingChange?.value_text ? (
            ratingChange.value_text
          ) : ratingChange?.value !== null && ratingChange?.value !== undefined ? (
            <span className="num" title="东财 ratingChange 数值码">
              {ratingChange.value}
            </span>
          ) : (
            <span className="content-list__metric-num">—</span>
          )}
        </ReportMetric>

        <ReportMetric label="目标价">
          {targetPrice?.value !== null && targetPrice?.value !== undefined ? (
            <>
              <NumberText value={targetPrice.value} /> <span>元</span>
            </>
          ) : (
            <span className="content-list__metric-num">—</span>
          )}
        </ReportMetric>

        {industry ? <ReportMetric label="行业">{industry}</ReportMetric> : null}
      </dl>

      <dl className="content-list__metrics content-list__metrics--report">
        <ReportMetric label="EPS 预测">
          {epsRows.length > 0 ? (
            epsRows.map((m) => (
              <span className="content-list__forecast" key={`${m.period}-${m.value}`}>
                <span className="content-list__metric-period">{m.period}</span>
                <NumberText value={m.value} />
              </span>
            ))
          ) : (
            <span className="content-list__metric-num">—</span>
          )}
        </ReportMetric>

        <ReportMetric label="PE 预测">
          {peRows.length > 0 ? (
            peRows.map((m) => (
              <span className="content-list__forecast" key={`${m.period}-${m.value}`}>
                <span className="content-list__metric-period">{m.period}</span>
                <NumberText value={m.value} />
                <span>倍</span>
              </span>
            ))
          ) : (
            <span className="content-list__metric-num">—</span>
          )}
        </ReportMetric>
      </dl>

      {coreView ? (
        <p className="content-list__core-view">{coreView}</p>
      ) : coreViewStatus === "not_extracted" ? (
        <p className="content-list__missing" data-testid="research-core-view-missing">
          核心观点未抽取（Plan 2 只落结构化元数据）
        </p>
      ) : null}
    </>
  );
}
