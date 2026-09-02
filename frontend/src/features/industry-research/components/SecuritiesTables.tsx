import { useMemo, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Button, Card, Table, Tooltip } from "antd";
import type { ColumnsType } from "antd/es/table";
import { COLORS } from "@/app/theme";
import {
  fetchIndustrySecurities,
  triggerFetchSecurities,
  type IndustrySecurities,
  type SecuritySeries,
} from "@/shared/api/industryResearch";
import { EChart, sparkOption } from "@/shared/ui/EChart";

const NUM_FONT = {
  fontFamily: '"Bahnschrift","Segoe UI",sans-serif',
  fontVariantNumeric: "tabular-nums" as const,
};

function formatNum(value: number | null, digits = 2): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function ChangeCell({ pct }: { pct: number | null }) {
  if (pct === null) return <span style={{ ...NUM_FONT, color: "#c9cdd4" }}>—</span>;
  const color = pct > 0 ? COLORS.up : pct < 0 ? COLORS.down : COLORS.flat;
  const arrow = pct > 0 ? "▲" : pct < 0 ? "▼" : "";
  return (
    <span style={{ ...NUM_FONT, fontWeight: 600, color }}>
      {arrow} {Math.abs(pct).toFixed(2)}%
    </span>
  );
}

function useSecuritiesColumns(withSpark: boolean): ColumnsType<SecuritySeries> {
  return useMemo(() => {
    const cols: ColumnsType<SecuritySeries> = [
      { title: "代码", key: "ts_code", render: (_, r) => <span style={NUM_FONT}>{r.tsCode}</span> },
      { title: "名称", key: "name", render: (_, r) => r.name ?? "—" },
      {
        title: "最新价",
        key: "close",
        align: "right",
        render: (_, r) => <span style={NUM_FONT}>{formatNum(r.latest?.close ?? null)}</span>,
      },
      {
        title: "涨跌幅",
        key: "change_pct",
        align: "right",
        render: (_, r) => <ChangeCell pct={r.changePct} />,
      },
      {
        title: "成交量",
        key: "volume",
        align: "right",
        render: (_, r) => <span style={NUM_FONT}>{formatNum(r.latest?.volume ?? null, 0)}</span>,
      },
    ];
    if (withSpark) {
      cols.push({
        title: "近期走势",
        key: "spark",
        width: 150,
        render: (_, r) => {
          const data = r.series.map((p) => p.close).filter((v): v is number => v !== null);
          return data.length > 2 ? (
            <EChart
              option={sparkOption(
                data,
                r.changePct !== null && r.changePct < 0 ? COLORS.down : COLORS.primary
              )}
              height={34}
              silent
            />
          ) : (
            <span style={{ color: "#c9cdd4", fontSize: 12 }}>—</span>
          );
        },
      });
    }
    return cols;
  }, [withSpark]);
}

function SecuritiesTable({
  data,
  loading,
  withSpark,
  emptyText,
}: {
  data?: IndustrySecurities;
  loading: boolean;
  withSpark: boolean;
  emptyText: string;
}) {
  const columns = useSecuritiesColumns(withSpark);
  return (
    <Table<SecuritySeries>
      rowKey="tsCode"
      size="small"
      loading={loading}
      columns={columns}
      dataSource={data?.codes ?? []}
      pagination={false}
      locale={{ emptyText }}
    />
  );
}

/**
 * 行情面（P5）：行业 ETF + 可转债两张紧凑表。
 * 代码清单由后端 registry 下发（securities_names 带展示名）；registry 无在市
 * 转债时转债表不渲染。「拉取数据」触发 fetch-securities 任务后延迟刷新（异步 ingest）。
 */
export function SecuritiesTables({ industryKey }: { industryKey: string }) {
  const [fetchError, setFetchError] = useState<string | null>(null);
  const etfQuery = useQuery({
    queryKey: ["industry-securities", industryKey, "etf"],
    queryFn: () => fetchIndustrySecurities(industryKey, "etf"),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  });
  const cbQuery = useQuery({
    queryKey: ["industry-securities", industryKey, "cb"],
    queryFn: () => fetchIndustrySecurities(industryKey, "cb"),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  });

  const trigger = useMutation({
    mutationFn: () => triggerFetchSecurities(industryKey),
    onSuccess: async () => {
      setFetchError(null);
      // ingest 经 MQ worker 异步完成，延迟后刷新两表
      await new Promise((r) => setTimeout(r, 3000));
      await Promise.all([etfQuery.refetch(), cbQuery.refetch()]);
    },
    onError: (err) => setFetchError(err.message),
  });

  const cbConfigured = (cbQuery.data?.codes ?? []).length > 0;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {fetchError && (
        <Alert
          type="error"
          showIcon
          message="拉取任务触发失败"
          description={fetchError}
          closable
          onClose={() => setFetchError(null)}
        />
      )}
      <Card
        size="small"
        title="行业 ETF"
        extra={
          <Button size="small" loading={trigger.isPending} onClick={() => trigger.mutate()}>
            拉取数据
          </Button>
        }
      >
        {etfQuery.error ? (
          <Alert
            type="error"
            showIcon
            message="ETF 数据加载失败"
            description={(etfQuery.error as Error).message}
            action={
              <a onClick={() => etfQuery.refetch()} style={{ cursor: "pointer" }}>
                重试
              </a>
            }
          />
        ) : (
          <SecuritiesTable
            data={etfQuery.data}
            loading={etfQuery.isLoading}
            withSpark
            emptyText="尚未拉取数据 — 点击右上角「拉取数据」回补近一年日线"
          />
        )}
      </Card>
      {cbConfigured && (
        <Card
          size="small"
          title="可转债"
          extra={
            <Tooltip title="正股为成分股的在市转债，由 registry 维护（cb_basic 核验），退市自动移除">
              <span style={{ fontSize: 11.5, color: "#86909c" }}>在市转债 · registry 维护</span>
            </Tooltip>
          }
        >
          {cbQuery.error ? (
            <Alert
              type="error"
              showIcon
              message="可转债数据加载失败"
              description={(cbQuery.error as Error).message}
              action={
                <a onClick={() => cbQuery.refetch()} style={{ cursor: "pointer" }}>
                  重试
                </a>
              }
            />
          ) : (
            <SecuritiesTable
              data={cbQuery.data}
              loading={cbQuery.isLoading}
              withSpark={false}
              emptyText="尚未拉取数据 — 点击上方「拉取数据」"
            />
          )}
        </Card>
      )}
    </div>
  );
}
