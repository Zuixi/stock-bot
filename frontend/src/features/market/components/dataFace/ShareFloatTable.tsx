import { Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchShareFloats, type ShareFloatItem } from "@/shared/api/marketData";
import { fmtYiGu } from "../format";

const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

export function ShareFloatTable() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["share-floats"],
    queryFn: () => fetchShareFloats(undefined, 30),
    staleTime: 5 * 60 * 1000,
  });

  const columns: ColumnsType<ShareFloatItem> = [
    { title: "解禁日", dataIndex: "floatDate", width: 100, render: (_, r) => <span style={NUM_FONT}>{r.floatDate}</span> },
    { title: "代码", dataIndex: "symbol", width: 80, render: (_, r) => <span style={NUM_FONT}>{r.symbol}</span> },
    { title: "名称", dataIndex: "name", width: 110, render: (_, r) => r.name ?? "—" },
    {
      title: "解禁数量", dataIndex: "floatShare", width: 100, align: "right",
      render: (_, r) => <span style={NUM_FONT}>{fmtYiGu(r.floatShare)}</span>,
    },
    {
      title: "占总股本", dataIndex: "floatRatio", width: 90, align: "right",
      render: (_, r) => <span style={NUM_FONT}>{r.floatRatio == null ? "—" : `${r.floatRatio.toFixed(2)}%`}</span>,
    },
    { title: "类型", dataIndex: "shareType", ellipsis: true, render: (_, r) => r.shareType ?? "—" },
  ];

  return (
    <Table<ShareFloatItem>
      rowKey={(r) => `${r.tsCode}-${r.floatDate}-${r.holderName}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 320 }}
      locale={{ emptyText: "暂无限售解禁数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/stock/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
