import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchRepurchases, type RepurchaseItem } from "@/shared/api/marketData";
import { fmtYi } from "../format";

const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

const PROC_COLOR: Record<string, string> = {
  实施: "blue",
  完成: "green",
};

export function RepurchaseTable() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["repurchases"],
    queryFn: () => fetchRepurchases(undefined, 30),
    staleTime: 5 * 60 * 1000,
  });

  const columns: ColumnsType<RepurchaseItem> = [
    { title: "公告日", dataIndex: "annDate", width: 100, render: (_, r) => <span style={NUM_FONT}>{r.annDate}</span> },
    { title: "代码", dataIndex: "symbol", width: 80, render: (_, r) => <span style={NUM_FONT}>{r.symbol}</span> },
    { title: "名称", dataIndex: "name", width: 100, render: (_, r) => r.name ?? "—" },
    {
      title: "进度", dataIndex: "proc", width: 110,
      render: (_, r) => <Tag color={PROC_COLOR[r.proc] ?? "default"} style={{ marginRight: 0 }}>{r.proc}</Tag>,
    },
    {
      title: "回购金额", dataIndex: "amount", width: 100, align: "right",
      render: (_, r) => <span style={{ ...NUM_FONT, fontWeight: 600 }}>{fmtYi(r.amount)}</span>,
    },
    {
      title: "回购数量", dataIndex: "vol", width: 100, align: "right",
      render: (_, r) => <span style={NUM_FONT}>{r.vol == null ? "—" : `${(r.vol / 1e4).toFixed(0)}万股`}</span>,
    },
  ];

  return (
    <Table<RepurchaseItem>
      rowKey={(r) => `${r.tsCode}-${r.annDate}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 320 }}
      locale={{ emptyText: "暂无回购数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/stock/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
