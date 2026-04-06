import { Table, Button, Empty, Tooltip } from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { ChangeText, NumberText } from "@/shared/ui";
import { useWatchlistStore } from "../store";
import { MOCK_STOCKS } from "@/shared/mocks/stocks";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { StockRecord } from "@/shared/types";
import type { ColumnsType } from "antd/es/table";

export function WatchlistTable() {
  const navigate = useNavigate();
  const { items, remove } = useWatchlistStore();

  const data = items
    .map((sym) => MOCK_STOCKS.find((s) => s.symbol === sym))
    .filter(Boolean) as StockRecord[];

  if (data.length === 0) {
    return (
      <Empty description="暂未添加自选股">
        <Button type="primary" onClick={() => navigate("/market")}>
          去市场页添加
        </Button>
      </Empty>
    );
  }

  const columns: ColumnsType<StockRecord> = [
    {
      title: "代码",
      dataIndex: "symbol",
      width: 80,
      render: (v: string) => <span style={{ fontFamily: "monospace" }}>{v}</span>,
    },
    {
      title: "名称",
      dataIndex: "name",
      width: 100,
      render: (v: string, r: StockRecord) => <a onClick={() => navigate(`/stock/${r.symbol}`)}>{v}</a>,
    },
    {
      title: "交易所",
      dataIndex: "exchange",
      width: 80,
      render: (v: string) => EXCHANGE_LABELS[v as keyof typeof EXCHANGE_LABELS] ?? v,
    },
    {
      title: "最新价",
      dataIndex: "latestPrice",
      width: 90,
      sorter: (a: StockRecord, b: StockRecord) => a.latestPrice - b.latestPrice,
      align: "right" as const,
      render: (v: number) => <NumberText value={v} />,
    },
    {
      title: "涨跌幅",
      dataIndex: "changePercent",
      width: 90,
      sorter: (a: StockRecord, b: StockRecord) => a.changePercent - b.changePercent,
      align: "right" as const,
      render: (v: number) => <ChangeText value={v} />,
    },
    {
      title: "总市值",
      dataIndex: "marketCap",
      width: 100,
      sorter: (a: StockRecord, b: StockRecord) => a.marketCap - b.marketCap,
      align: "right" as const,
      render: (v: number) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "",
      key: "action",
      width: 48,
      render: (_: unknown, record: StockRecord) => (
        <Tooltip title="移除自选">
          <Button
            type="text"
            size="small"
            danger
            icon={<DeleteOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              remove(record.symbol);
            }}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <Table<StockRecord>
      columns={columns}
      dataSource={data}
      rowKey="symbol"
      size="small"
      pagination={false}
      onRow={(record) => ({
        style: { cursor: "pointer" },
        onClick: () => navigate(`/stock/${record.symbol}`),
      })}
    />
  );
}
