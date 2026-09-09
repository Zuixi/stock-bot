import { Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchBlockTrades, type BlockTradeItem } from "@/shared/api/marketData";
import { fmtWanGu, fmtWanYi } from "../format";

const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

export function BlockTradeTable() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["block-trades"],
    queryFn: () => fetchBlockTrades(undefined, 15),
    staleTime: 5 * 60 * 1000,
  });

  const columns: ColumnsType<BlockTradeItem> = [
    { title: "代码", dataIndex: "symbol", width: 80, render: (_, r) => <span style={NUM_FONT}>{r.symbol}</span> },
    { title: "名称", dataIndex: "name", width: 90, render: (_, r) => r.name ?? "—" },
    {
      title: "成交价", dataIndex: "price", width: 80, align: "right",
      render: (_, r) => <span style={NUM_FONT}>{r.price == null ? "—" : r.price.toFixed(2)}</span>,
    },
    {
      title: "成交量", dataIndex: "volume", width: 90, align: "right",
      render: (_, r) => <span style={NUM_FONT}>{fmtWanGu(r.volume)}</span>,
    },
    {
      title: "成交额", dataIndex: "amount", width: 90, align: "right",
      render: (_, r) => <span style={{ ...NUM_FONT, fontWeight: 600 }}>{fmtWanYi(r.amount)}</span>,
    },
    { title: "买方营业部", dataIndex: "buyer", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.buyer ?? "—"}</Typography.Text> },
    { title: "卖方营业部", dataIndex: "seller", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.seller ?? "—"}</Typography.Text> },
  ];

  return (
    <Table<BlockTradeItem>
      rowKey={(r) => `${r.tsCode}-${r.buyer}-${r.price}-${r.volume}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 320 }}
      locale={{ emptyText: "暂无大宗交易数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/stock/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
