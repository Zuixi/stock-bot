import { useQuery } from "@tanstack/react-query";
import { Alert, Descriptions, Drawer, Skeleton, Table, Tag, Typography } from "antd";

import { fetchProvenance, type ProvenanceQuality, type ProvenanceSource } from "@/shared/api/provenance";

interface Props {
  open: boolean;
  metricKey: string | null;
  asOf?: string | null;
  scopeKey?: string | null;
  onClose: () => void;
}

const QUALITY_COLOR: Record<ProvenanceQuality["status"], string> = {
  pass: "success",
  warn: "warning",
  fail: "error",
  error: "default",
};

const SOURCE_COLUMNS = [
  { title: "来源表", dataIndex: "table", key: "table" },
  { title: "域", dataIndex: "domain", key: "domain" },
  { title: "当日行数", dataIndex: "rows", key: "rows", align: "right" as const },
  {
    title: "批次",
    key: "batch",
    render: (_: unknown, row: ProvenanceSource) =>
      row.batch_id ? `#${row.batch_id}${row.batch_status ? ` · ${row.batch_status}` : ""}` : "—",
  },
  {
    title: "原始留档",
    key: "raw",
    render: (_: unknown, row: ProvenanceSource) =>
      row.raw_path ? <Typography.Text copyable ellipsis={{ tooltip: row.raw_path }}>{row.raw_path}</Typography.Text> : "—",
  },
];

/**
 * 数字溯源抽屉（P7）：显示值 → 口径 → 来源表/行数 → 批次 → 原始 JSONL → 质量/新鲜度。
 *
 * 只在打开时发请求（`enabled: open`），不占首屏预算；失败不阻塞主内容。
 */
export function ProvenanceDrawer({ open, metricKey, asOf, scopeKey, onClose }: Props) {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["provenance", metricKey, asOf ?? null, scopeKey ?? null],
    queryFn: () => fetchProvenance(metricKey as string, { date: asOf, scopeKey }),
    enabled: open && Boolean(metricKey),
    staleTime: 60_000,
  });

  return (
    <Drawer
      open={open}
      onClose={onClose}
      width={560}
      destroyOnClose
      title={data ? `${data.metric.label} · 数据溯源` : "数据溯源"}
      data-testid="provenance-drawer"
    >
      {isLoading ? <Skeleton active paragraph={{ rows: 8 }} /> : null}
      {isError ? (
        <Alert
          type="warning"
          showIcon
          message="溯源暂不可用"
          description="这不代表数字是错的；只是这条链路暂时读不到。主页面内容不受影响。"
        />
      ) : null}
      {data ? (
        <div style={{ display: "grid", gap: 16 }}>
          <Descriptions size="small" column={1} bordered>
            <Descriptions.Item label="值">
              {data.value.value == null ? "—" : `${data.value.value} ${data.metric.unit}`}
            </Descriptions.Item>
            <Descriptions.Item label="数据日">{data.value.as_of}</Descriptions.Item>
            <Descriptions.Item label="口径版本">
              <Tag>{data.metric.caliber_version}</Tag>
            </Descriptions.Item>
            <Descriptions.Item label="定义">{data.metric.definition}</Descriptions.Item>
            <Descriptions.Item label="新鲜度">
              {data.freshness.expected_as_of ?? "—"}
              {data.freshness.lag_trading_days != null
                ? ` · 落后 ${data.freshness.lag_trading_days} 个交易日`
                : ""}
              {data.freshness.stale ? <Tag color="warning" style={{ marginLeft: 8 }}>stale</Tag> : null}
            </Descriptions.Item>
          </Descriptions>

          <div>
            <Typography.Text strong>来源链路</Typography.Text>
            <Table<ProvenanceSource>
              size="small"
              style={{ marginTop: 8 }}
              rowKey={(row) => `${row.table}-${row.domain}`}
              pagination={false}
              columns={SOURCE_COLUMNS}
              dataSource={data.sources}
            />
          </div>

          <div>
            <Typography.Text strong>质量断言</Typography.Text>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
              {data.quality.length === 0 ? (
                <Typography.Text type="secondary">该日尚无质量断言记录</Typography.Text>
              ) : (
                data.quality.map((item) => (
                  <Tag key={item.key} color={QUALITY_COLOR[item.status]} title={item.description}>
                    {item.key.replace("dq.", "")} · {item.status}
                  </Tag>
                ))
              )}
            </div>
          </div>

          <div>
            <Typography.Text strong>消费端点</Typography.Text>
            <div style={{ display: "grid", gap: 2, marginTop: 6 }}>
              {data.metric.consumers.map((path) => (
                <Typography.Text key={path} type="secondary" className="num">
                  {path}
                </Typography.Text>
              ))}
            </div>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}
