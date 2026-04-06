import { Table, Button, Tooltip } from "antd";
import { StarOutlined, StarFilled } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { ChangeText, NumberText } from "@/shared/ui";
import { useWatchlistStore } from "@/features/watchlist/store";
import type { StockRecord } from "@/shared/types";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { ColumnsType, TableProps } from "antd/es/table";

interface Props {
  data: StockRecord[];
  loading?: boolean;
  onChange?: TableProps<StockRecord>["onChange"];
}

export function StockTable({ data, loading, onChange }: Props) {
  const navigate = useNavigate();
  const { items, toggle } = useWatchlistStore();

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
      align: "right" as const,
      render: (v: number) => <NumberText value={v} />,
    },
    {
      title: "涨跌幅",
      dataIndex: "changePercent",
      width: 90,
      sorter: true,
      align: "right" as const,
      render: (v: number) => <ChangeText value={v} />,
    },
    {
      title: "成交额",
      dataIndex: "turnover",
      width: 100,
      sorter: true,
      align: "right" as const,
      render: (v: number) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "总市值",
      dataIndex: "marketCap",
      width: 100,
      sorter: true,
      defaultSortOrder: "descend" as const,
      align: "right" as const,
      render: (v: number) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "PE(TTM)",
      dataIndex: "pe",
      width: 80,
      sorter: true,
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
      pagination={{ pageSize: 20, showSizeChanger: true, showTotal: (t) => `共 ${t} 条` }}
      onChange={onChange}
      onRow={(record) => ({
        style: { cursor: "pointer" },
        onClick: () => navigate(`/stock/${record.symbol}`),
      })}
    />
  );
}
