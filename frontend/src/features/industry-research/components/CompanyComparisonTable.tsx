import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { Alert, Table, Tag } from "antd";
import type { ColumnsType, ColumnType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import {
  fetchIndustryCompanies,
  type CompanyRow,
} from "@/shared/api/industryResearch";
import { SourceBadge } from "@/features/industry-research/components/SourceBadge";

/** 固定列 key → 行字段（与后端 CompanyRowOut 对齐）；其余 key 一律读 row.metrics[key] */
const FIXED_FIELDS: Record<string, keyof CompanyRow> = {
  symbol: "symbol",
  name: "name",
  latest_price: "latestPrice",
  total_mv_yi: "totalMvYi",
  pe_ttm: "peTtm",
  pb: "pb",
};

function readCell(row: CompanyRow, key: string): number | string | null {
  if (key in FIXED_FIELDS) {
    return row[FIXED_FIELDS[key]] as number | string | null;
  }
  return row.metrics[key] ?? null;
}

function formatCell(value: number | string | null): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return Math.abs(value) >= 1000
    ? value.toLocaleString("zh-CN", { maximumFractionDigits: 0 })
    : value.toLocaleString("zh-CN", { maximumFractionDigits: 2 });
}

/**
 * 标的分析 · 成分股对比表（P5）
 * 列由后端 registry 下发（固定行情/估值列 + company 分组指标列），
 * 新增公司指标零前端改动；行点击跳转个股详情 /stock/:symbol。
 */
export function CompanyComparisonTable({ industryKey }: { industryKey: string }) {
  const navigate = useNavigate();
  const { data, isLoading, error, refetch } = useQuery({
    queryKey: ["industry-companies", industryKey],
    queryFn: () => fetchIndustryCompanies(industryKey),
    staleTime: 60 * 1000,
    refetchOnWindowFocus: false,
  });

  const columns: ColumnsType<CompanyRow> = useMemo(() => {
    if (!data) return [];
    return data.columns.map((col): ColumnType<CompanyRow> => {
      const read = (row: CompanyRow) => readCell(row, col.key);
      return {
        title: (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            {col.label}
            {col.tier && <SourceBadge tier={col.tier} />}
          </span>
        ),
        key: col.key,
        align: col.numeric ? "right" : "left",
        render: (_: unknown, row: CompanyRow) => {
          if (col.key === "name") {
            return (
              <span>
                {row.name}
                {row.hasCompanyData && (
                  <Tag
                    color="purple"
                    style={{ marginLeft: 6, fontSize: 10, lineHeight: "14px", paddingInline: 4 }}
                  >
                    跟踪中
                  </Tag>
                )}
              </span>
            );
          }
          const value = read(row);
          return (
            <span
              style={{
                fontFamily: col.numeric
                  ? '"Bahnschrift","Segoe UI",sans-serif'
                  : undefined,
                fontVariantNumeric: col.numeric ? "tabular-nums" : undefined,
                color: value === null ? "#c9cdd4" : undefined,
              }}
            >
              {formatCell(value)}
            </span>
          );
        },
        sorter: (a, b) => {
          const av = read(a);
          const bv = read(b);
          if (av === null) return -1;
          if (bv === null) return 1;
          if (typeof av === "number" && typeof bv === "number") return av - bv;
          return String(av).localeCompare(String(bv));
        },
      };
    });
  }, [data]);

  if (error) {
    return (
      <Alert
        type="error"
        showIcon
        message="成分股数据加载失败"
        description={error.message}
        action={
          <a onClick={() => refetch()} style={{ cursor: "pointer" }}>
            重试
          </a>
        }
      />
    );
  }

  return (
    <Table<CompanyRow>
      rowKey="symbol"
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data?.rows ?? []}
      pagination={false}
      scroll={{ x: "max-content" }}
      locale={{ emptyText: "该行业暂无成分股数据" }}
      onRow={(record) => ({
        onClick: () => navigate(`/stock/${record.symbol}`),
        style: { cursor: "pointer" },
      })}
    />
  );
}
