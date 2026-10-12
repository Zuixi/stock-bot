import { useNavigate } from "react-router-dom";
import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ChangeText } from "@/shared/ui";
import type { RankingBoardItem } from "@/features/market/rankings";

interface Props {
  items: RankingBoardItem[];
  /** 排序方向：决定数值列的语义标题（涨幅 vs 跌幅） */
  sortDirection: "DESC" | "ASC";
  onRowClick: (symbol: string) => void;
}

/** 亿元/万元等大数展示：`null` → `--`（缺失与 0 不同义）。 */
function fmtNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return value.toLocaleString("zh-CN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

/** 市值（万元）→ 亿元，避免榜上出现 13 位数字。 */
function fmtMarketCapYi(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "--";
  return (value / 1e4).toFixed(2);
}

/**
 * 一张榜的 100 强表。
 *
 * **整行可点 → `/company/:symbol`**（spec §S1.6 的下钻要求）；用 `useNavigate` 而非
 * 外层包 `<Link>`：Table 的 `rowKey` 单元格里有自己的交互（如 ST 标签 tooltip），
 * 包一层 `<a>` 会把整行语义变成链接并可能吞掉单元格事件。
 *
 * 每个榜只展示"它自己那一个指标列"：7 榜共用一套后端 schema，但 UI 一次只看一个
 * 榜——把 7 列都摆出来会让 6 列永远是 `--`。
 */
export function RankingBoardTable({ items, sortDirection, onRowClick }: Props) {
  const isMonthly = items.some((i) => i.monthly_pct_chg !== null && i.monthly_pct_chg !== undefined);

  const columns: ColumnsType<RankingBoardItem> = [
    {
      title: "#",
      dataIndex: "rank",
      width: 64,
      align: "right",
      render: (rank: number) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{rank}</span>,
    },
    {
      title: "标的",
      dataIndex: "name",
      render: (_: unknown, row: RankingBoardItem) => (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
          <span>{row.name}</span>
          <span style={{ color: "var(--text-secondary)", fontSize: 12 }}>{row.symbol}</span>
          {row.is_st && (
            <Tag color="warning" style={{ marginInlineEnd: 0 }}>
              ST
            </Tag>
          )}
          {row.is_new_stock && (
            <Tag style={{ marginInlineEnd: 0 }}>次新</Tag>
          )}
        </span>
      ),
    },
  ];

  if (isMonthly) {
    columns.push({
      title: sortDirection === "DESC" ? `月度涨幅` : `月度跌幅`,
      dataIndex: "monthly_pct_chg",
      width: 120,
      align: "right",
      render: (value: number | null | undefined) => <ChangeText value={value} />,
    });
  } else {
    columns.push({
      title: "指标值",
      dataIndex: "metric",
      width: 180,
      align: "right",
      render: (_: unknown, row: RankingBoardItem) => {
        if (row.n_income_attr_p !== null && row.n_income_attr_p !== undefined) {
          // 利润单位是元；亿元更好读，但单位标注必须跟着变（不能只改数字）
          return (
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {fmtNumber(row.n_income_attr_p / 1e8)} <span style={{ color: "var(--text-secondary)" }}>亿元</span>
            </span>
          );
        }
        if (row.roe !== null && row.roe !== undefined) {
          // ROE 是质量指标，**不套涨跌色**：红色在这里会被读成"高收益/涨"，语义错位。
          return (
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {row.roe.toFixed(2)} <span style={{ color: "var(--text-secondary)" }}>%</span>
            </span>
          );
        }
        if (row.total_mv !== null && row.total_mv !== undefined) {
          return (
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {fmtMarketCapYi(row.total_mv)}{" "}
              <span style={{ color: "var(--text-secondary)" }}>亿元</span>
            </span>
          );
        }
        if (row.dv_ratio !== null && row.dv_ratio !== undefined) {
          return (
            <span style={{ fontVariantNumeric: "tabular-nums" }}>
              {row.dv_ratio.toFixed(2)} <span style={{ color: "var(--text-secondary)" }}>%</span>
            </span>
          );
        }
        return <span className="delta delta--flat">--</span>;
      },
    });
  }

  columns.push({
    title: "最新价",
    dataIndex: "close",
    width: 100,
    align: "right",
    render: (value: number | null | undefined) => (
      <span style={{ fontVariantNumeric: "tabular-nums" }}>{fmtNumber(value)}</span>
    ),
  });
  columns.push({
    title: "当日涨跌",
    dataIndex: "pct_chg",
    width: 110,
    align: "right",
    render: (value: number | null | undefined) => <ChangeText value={value} />,
  });

  const usesFinancialPeriod = items.some((i) => i.financial_report);
  if (usesFinancialPeriod) {
    columns.push({
      title: "财务期",
      dataIndex: "financial_report",
      width: 200,
      render: (_: unknown, row: RankingBoardItem) => {
        const report = row.financial_report;
        if (!report) {
          // 该榜是财务类榜，这一行却没有报告期 → 只可能是后端形状变化，如实显示缺失
          return <span style={{ color: "var(--text-secondary)" }}>--</span>;
        }
        return (
          <span style={{ fontSize: 12 }}>
            {report.reportType} {report.endDate}
            <br />
            <span style={{ color: "var(--text-secondary)" }}>
              公告 {report.annDate}
              {report.annDateSource === "ann_date" ? "（公告日口径）" : ""}
            </span>
          </span>
        );
      },
    });
  }

  return (
    <Table<RankingBoardItem>
      size="small"
      rowKey="symbol"
      columns={columns}
      dataSource={items}
      pagination={false}
      scroll={{ y: 560 }}
      onRow={(row) => ({
        onClick: () => onRowClick(row.symbol),
        style: { cursor: "pointer" },
      })}
      locale={{ emptyText: "本榜口径下当前没有可排序标的" }}
    />
  );
}
