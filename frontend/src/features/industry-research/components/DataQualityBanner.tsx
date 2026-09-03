import { Alert, Collapse, Descriptions, Progress, Space, Tag, Typography } from "antd";
import type { DataQuality } from "@/shared/api/industryResearch";

interface Props {
  quality: DataQuality;
  signalIsStale: boolean;
}

const STATUS_TEXT: Record<string, string> = {
  healthy: "数据质量正常",
  degraded: "数据质量下降",
  unavailable: "数据质量异常",
  demo: "演示数据",
};

const DETAIL_STATUS_TEXT: Record<string, string> = {
  ready: "就绪",
  missing: "缺失",
  stale: "过期",
  source_rejected: "来源不符合要求",
  partial: "覆盖不足",
};

const ISSUE_STATUSES = new Set(["missing", "stale", "source_rejected", "partial"]);

function detailDescription(detail: DataQuality["details"][number]) {
  const parts = [detail.reason];
  if (detail.period) parts.push(`期次 ${detail.period}`);
  if (detail.source) parts.push(`来源 ${detail.source}`);
  if (detail.entityCoverage !== null) {
    parts.push(`覆盖率 ${Math.round(detail.entityCoverage * 100)}%`);
  }
  return parts.filter(Boolean).join(" · ") || "等待数据更新";
}

export function DataQualityBanner({ quality, signalIsStale }: Props) {
  const issues = quality.details.filter((detail) => ISSUE_STATUSES.has(detail.status));
  const total = quality.readyCount + quality.missingCount + quality.staleCount
    + quality.rejectedCount + quality.partialCount;
  const readyPercent = total > 0 ? Math.round((quality.readyCount / total) * 100) : 0;
  const statusText = STATUS_TEXT[quality.status] ?? "数据质量状态未知";

  if (quality.status === "healthy") {
    return (
      <div data-testid="industry-data-quality">
        <Space size={8} wrap>
          <Tag color="success">{statusText}</Tag>
          <Typography.Text type="secondary">
            {quality.readyCount} 项指标就绪 · 截至 {quality.asOf}
          </Typography.Text>
        </Space>
      </div>
    );
  }

  const description = (
    <Space direction="vertical" size={8} style={{ width: "100%" }}>
      {signalIsStale && (
        <Typography.Text>
          当前展示为最近一次有效信号，本次因数据不足未更新
        </Typography.Text>
      )}
      {issues.slice(0, 3).map((detail) => (
        <div key={detail.metricKey}>
          <Typography.Text strong>{detail.metricKey}</Typography.Text>
          <Typography.Text type="secondary">：{detailDescription(detail)}</Typography.Text>
        </div>
      ))}
      <Space size={12} wrap>
        <Progress percent={readyPercent} size="small" style={{ width: 180 }} />
        <Typography.Text type="secondary">
          就绪 {quality.readyCount} · 缺失 {quality.missingCount} · 过期 {quality.staleCount}
          {quality.rejectedCount > 0 ? ` · 来源拒绝 ${quality.rejectedCount}` : ""}
          {quality.partialCount > 0 ? ` · 覆盖不足 ${quality.partialCount}` : ""}
        </Typography.Text>
      </Space>
      {quality.details.length > 0 && (
        <Collapse
          ghost
          size="small"
          items={[{
            key: "details",
            label: `查看全部 ${quality.details.length} 项指标明细`,
            children: (
              <Descriptions
                size="small"
                column={1}
                items={quality.details.map((detail) => ({
                  key: detail.metricKey,
                  label: detail.metricKey,
                  children: (
                    <Space size={8} wrap>
                      <Tag color={detail.status === "ready" ? "success" : "warning"}>
                        {DETAIL_STATUS_TEXT[detail.status] ?? detail.status}
                      </Tag>
                      <span>{detailDescription(detail)}</span>
                    </Space>
                  ),
                }))}
              />
            ),
          }]}
        />
      )}
    </Space>
  );

  return (
    <div data-testid="industry-data-quality">
      <Alert
        showIcon
        type={quality.status === "unavailable" ? "warning" : "info"}
        message={statusText}
        description={description}
      />
    </div>
  );
}
