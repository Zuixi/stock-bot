import { Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDragonTiger, type DragonTigerItem } from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtSignedYi } from "../format";

const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

export function DragonTigerTable() {
  const navigate = useNavigate();
  const { data = [], isLoading } = useQuery({
    queryKey: ["dragon-tiger"],
    queryFn: () => fetchDragonTiger(15),
    staleTime: 5 * 60 * 1000,
  });

  const columns: ColumnsType<DragonTigerItem> = [
    { title: "代码", dataIndex: "symbol", width: 80, render: (_, r) => <span style={NUM_FONT}>{r.symbol}</span> },
    { title: "名称", dataIndex: "name", width: 90, render: (_, r) => r.name ?? "—" },
    {
      title: "涨跌幅", dataIndex: "pctChange", width: 80, align: "right",
      render: (_, r) => (
        <span style={{ ...NUM_FONT, color: (r.pctChange ?? 0) > 0 ? COLORS.up : (r.pctChange ?? 0) < 0 ? COLORS.down : COLORS.flat }}>
          {r.pctChange == null ? "—" : `${r.pctChange > 0 ? "+" : ""}${r.pctChange.toFixed(2)}%`}
        </span>
      ),
    },
    {
      title: "龙虎榜净买额", dataIndex: "netAmount", width: 110, align: "right",
      render: (_, r) => (
        <span style={{ ...NUM_FONT, fontWeight: 600, color: (r.netAmount ?? 0) > 0 ? COLORS.up : (r.netAmount ?? 0) < 0 ? COLORS.down : COLORS.flat }}>
          {fmtSignedYi(r.netAmount)}
        </span>
      ),
    },
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];

  return (
    <Table<DragonTigerItem>
      rowKey={(r) => `${r.tsCode}-${r.reason}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ y: 320 }}
      locale={{ emptyText: "暂无龙虎榜数据（盘后自动更新）" }}
      onRow={(r) => ({ onClick: () => navigate(`/stock/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
