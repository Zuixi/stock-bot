import { Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchRepurchases, type RepurchaseItem } from "@/shared/api/marketData";
import {
  INSTRUMENT_COL_WIDTH,
  InstrumentIdentity,
  TRIPLET_WIDTH,
} from "@/shared/ui";
import { useMarketPolling } from "../../hooks/useMarketPolling";
import { fmtYi } from "../format";

const PROC_COLOR: Record<string, string> = {
  实施: "blue",
  完成: "green",
};

const DATE_WIDTH = 100;
const PROC_WIDTH = 96;
const MIN_TABLE_WIDTH =
  DATE_WIDTH + INSTRUMENT_COL_WIDTH.identity + PROC_WIDTH + TRIPLET_WIDTH.price + INSTRUMENT_COL_WIDTH.extra;

/**
 * 回购清单（P1-2）。
 *
 * ⚠️ 载荷里**没有行情/涨跌口径**：`RepurchaseOut` 只有 `proc` / `vol`（股）/ `amount`（元）。
 * 回购是公司行为公告，与"今天涨跌"无关，故不塞恒为 `--` 的涨跌列。这里只做**几何统一**：
 * 标识格与标的清单同形（名称 + 小号代码），回购金额沿用三元组的价格列宽（72px），
 * 数字一律 `.num` + 右对齐，缺失一律 `--`。
 */
export function RepurchaseTable() {
  const navigate = useNavigate();
  const { refetchInterval } = useMarketPolling();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market", "repurchases"],
    queryFn: () => fetchRepurchases(undefined, 30),
    staleTime: 5 * 60 * 1000,
    refetchInterval,
  });

  const columns: ColumnsType<RepurchaseItem> = [
    {
      title: "公告日", dataIndex: "annDate", width: DATE_WIDTH,
      render: (_, r) => <span className="num">{r.annDate}</span>,
    },
    {
      // 余量列：不声明 width（宽屏吸收剩余宽度，数值列因此保持声明列宽；见 MIN_TABLE_WIDTH）
      className: "inst-cell--identity",
      title: "名称",
      dataIndex: "name",
      render: (_, r) => <InstrumentIdentity symbol={r.symbol} name={r.name ?? "—"} />,
    },
    {
      title: "进度", dataIndex: "proc", width: PROC_WIDTH,
      render: (_, r) => <Tag color={PROC_COLOR[r.proc] ?? "default"} style={{ marginRight: 0 }}>{r.proc}</Tag>,
    },
    {
      title: "回购金额", dataIndex: "amount", width: TRIPLET_WIDTH.price, align: "right",
      render: (_, r) => <span className="num" style={{ fontWeight: 600 }}>{fmtYi(r.amount)}</span>,
    },
    {
      title: "回购数量", dataIndex: "vol", width: INSTRUMENT_COL_WIDTH.extra, align: "right",
      render: (_, r) => <span className="num">{r.vol == null ? "--" : `${(r.vol / 1e4).toFixed(0)}万股`}</span>,
    },
  ];

  return (
    <Table<RepurchaseItem>
      rowKey={(r) => `${r.tsCode}-${r.annDate}-${r.proc}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      // 固定表布局 + `scroll.x`：声明了 width 的列**逐列精确**，唯一没声明 width 的
      // "余量列"吸收宽屏剩余空间、窄屏守住下限（见 MIN_TABLE_WIDTH 注释）。
      tableLayout="fixed"
      pagination={false}
      scroll={{ x: MIN_TABLE_WIDTH, y: 320 }}
      locale={{ emptyText: "暂无回购数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/company/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
