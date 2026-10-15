import { Table } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchShareFloats, type ShareFloatItem } from "@/shared/api/marketData";
import {
  INSTRUMENT_COL_WIDTH,
  InstrumentIdentity,
  TRIPLET_WIDTH,
} from "@/shared/ui";
import { useMarketPolling } from "../../hooks/useMarketPolling";
import { fmtYiGu } from "../format";

const DATE_WIDTH = 100;
/** "余量列"（类型，不声明 width）的下限：宽屏吸收剩余宽度，窄屏不许低于此值 */
const TYPE_MIN_WIDTH = 140;
const MIN_TABLE_WIDTH =
  DATE_WIDTH + INSTRUMENT_COL_WIDTH.identity + INSTRUMENT_COL_WIDTH.extra + TRIPLET_WIDTH.pct + TYPE_MIN_WIDTH;

/**
 * 限售解禁清单（P1-2）。
 *
 * ⚠️ 载荷里**没有任何行情/涨跌口径**：`ShareFloatOut` 只有 `float_date` / `float_share`
 * （万股）/ `float_ratio`（%）/ `share_type`——解禁是"未来某日有多少股可流通"，
 * 与"今天涨跌"无关，故不塞恒为 `--` 的涨跌列。这里只做**几何统一**：
 * 标识格与标的清单同形（名称 + 小号代码），占比沿用涨跌幅的百分比列宽
 * （66px，`TRIPLET_WIDTH.pct`）以对齐其它清单的百分比列，数字一律 `.num` + 右对齐，
 * 缺失一律 `--`。
 */
export function ShareFloatTable() {
  const navigate = useNavigate();
  const { refetchInterval } = useMarketPolling();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market", "share-floats"],
    queryFn: () => fetchShareFloats(undefined, 30),
    staleTime: 5 * 60 * 1000,
    refetchInterval,
  });

  const columns: ColumnsType<ShareFloatItem> = [
    {
      title: "解禁日", dataIndex: "floatDate", width: DATE_WIDTH,
      render: (_, r) => <span className="num">{r.floatDate}</span>,
    },
    {
      className: "inst-cell--identity",
      title: "名称",
      dataIndex: "name",
      width: INSTRUMENT_COL_WIDTH.identity,
      render: (_, r) => <InstrumentIdentity symbol={r.symbol} name={r.name ?? "—"} />,
    },
    {
      title: "解禁数量", dataIndex: "floatShare", width: INSTRUMENT_COL_WIDTH.extra, align: "right",
      render: (_, r) => <span className="num">{fmtYiGu(r.floatShare)}</span>,
    },
    {
      title: "占总股本", dataIndex: "floatRatio", width: TRIPLET_WIDTH.pct, align: "right",
      render: (_, r) => <span className="num">{r.floatRatio == null ? "--" : `${r.floatRatio.toFixed(2)}%`}</span>,
    },
    { title: "类型", dataIndex: "shareType", className: "inst-cell--flex", ellipsis: true, render: (_, r) => r.shareType ?? "—" },
  ];

  return (
    <Table<ShareFloatItem>
      rowKey={(r) => `${r.tsCode}-${r.floatDate}-${r.holderName}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      // 固定表布局 + `scroll.x`：声明了 width 的列**逐列精确**，唯一没声明 width 的
      // "余量列"吸收宽屏剩余空间、窄屏守住下限（见 MIN_TABLE_WIDTH 注释）。
      tableLayout="fixed"
      pagination={false}
      scroll={{ x: MIN_TABLE_WIDTH, y: 320 }}
      locale={{ emptyText: "暂无限售解禁数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/company/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
