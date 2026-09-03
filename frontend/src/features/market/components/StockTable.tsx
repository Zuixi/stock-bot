import { Table, Button, Tooltip } from "antd";
import { StarOutlined, StarFilled } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useEffect, useState } from "react";
import { ChangeText, NumberText } from "@/shared/ui";
import { useWatchlistStore } from "@/features/watchlist/store";
import type { StockRecord } from "@/shared/types";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { ColumnsType, TableProps } from "antd/es/table";

interface Props {
  data: StockRecord[];
  total?: number;
  current?: number;
  pageSize?: number;
  loading?: boolean;
  onChange?: TableProps<StockRecord>["onChange"];
  /** Controlled sort — parent owns the sort state, StockTable only shows the indicator. */
  sortBy?: keyof StockRecord;
  sortOrder?: "ascend" | "descend";
}

export function StockTable({ data, total, current, pageSize, loading, onChange, sortBy, sortOrder }: Props) {
  const navigate = useNavigate();
  const { items, toggle } = useWatchlistStore();
  const [paginationState, setPaginationState] = useState<{ current: number; pageSize: number }>({
    current: current ?? 1,
    pageSize: pageSize ?? 20,
  });

  useEffect(() => {
    if (current === undefined && pageSize === undefined) return;
    setPaginationState((prev) => ({
      current: current ?? prev.current,
      pageSize: pageSize ?? prev.pageSize,
    }));
  }, [current, pageSize]);

  const totalCount = total ?? data.length;
  const isControlled = current !== undefined || pageSize !== undefined;

  useEffect(() => {
    if (isControlled) return;
    const safePageSize = Math.max(1, paginationState.pageSize);
    const maxPage = Math.max(1, Math.ceil(totalCount / safePageSize));
    if (paginationState.current > maxPage) {
      setPaginationState((prev) => ({ ...prev, current: maxPage }));
    }
  }, [isControlled, paginationState.current, paginationState.pageSize, totalCount]);

  const currentPage = current ?? paginationState.current;
  const currentPageSize = pageSize ?? paginationState.pageSize;

  const handleTableChange: TableProps<StockRecord>["onChange"] = (
    pagination,
    filters,
    sorter,
    extra
  ) => {
    const nextPageSize = Math.max(1, pagination.pageSize ?? currentPageSize);
    const nextCurrent = nextPageSize !== currentPageSize ? 1 : (pagination.current ?? currentPage);
    if (!isControlled) {
      setPaginationState({ current: nextCurrent, pageSize: nextPageSize });
    }
    onChange?.(
      { ...pagination, current: nextCurrent, pageSize: nextPageSize, total: totalCount },
      filters,
      sorter,
      extra
    );
  };

  const columns: ColumnsType<StockRecord> = [
    {
      title: "代码",
      dataIndex: "symbol",
      width: 80,
      fixed: "left" as const,
      render: (v: string) => <span style={{ fontFamily: "monospace" }}>{v}</span>,
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 100,
      fixed: "left" as const,
      render: (v: string, record: StockRecord) => (
        <a onClick={() => navigate(`/stock/${record.symbol}`)}>{v}</a>
      ),
    },
    {
      title: "交易所",
      dataIndex: "exchange",
      width: 80,
      render: (v: string) => EXCHANGE_LABELS[v as keyof typeof EXCHANGE_LABELS] ?? v,
    },
    {
      title: "行业",
      dataIndex: "industry",
      width: 90,
    },
    {
      title: "最新价",
      dataIndex: "latestPrice",
      width: 90,
      sorter: true,
      sortOrder: sortBy === "latestPrice" ? sortOrder : undefined,
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} />,
    },
    {
      title: "涨跌幅",
      dataIndex: "changePercent",
      width: 90,
      sorter: true,
      sortOrder: sortBy === "changePercent" ? sortOrder : undefined,
      align: "right" as const,
      render: (v: number | undefined) => <ChangeText value={v} />,
    },
    {
      title: "成交额",
      dataIndex: "turnover",
      width: 100,
      sorter: true,
      sortOrder: sortBy === "turnover" ? sortOrder : undefined,
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "总市值",
      dataIndex: "marketCap",
      width: 100,
      sorter: true,
      sortOrder: sortBy === "marketCap" ? sortOrder : undefined,
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "PE(TTM)",
      dataIndex: "pe",
      width: 80,
      sorter: true,
      sortOrder: sortBy === "pe" ? sortOrder : undefined,
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} />,
    },
    {
      title: "",
      key: "action",
      width: 48,
      fixed: "right" as const,
      render: (_: unknown, record: StockRecord) => {
        const isWatched = items.includes(record.symbol);
        return (
          <Tooltip title={isWatched ? "取消自选" : "加入自选"}>
            <Button
              type="text"
              size="small"
              icon={isWatched ? <StarFilled style={{ color: "#faad14" }} /> : <StarOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                toggle(record.symbol);
              }}
            />
          </Tooltip>
        );
      },
    },
  ];

  return (
    <Table<StockRecord>
      columns={columns}
      dataSource={data}
      rowKey="symbol"
      size="small"
      loading={loading}
      scroll={{ x: 900 }}
      pagination={{
        current: currentPage,
        pageSize: currentPageSize,
        total: totalCount,
        showSizeChanger: true,
        showTotal: (t) => `共 ${t} 条`,
      }}
      onChange={handleTableChange}
      onRow={(record) => ({
        style: { cursor: "pointer" },
        onClick: () => navigate(`/stock/${record.symbol}`),
      })}
    />
  );
}
