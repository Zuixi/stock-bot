import { Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchBlockTrades, type BlockTradeItem } from "@/shared/api/marketData";
import {
  INSTRUMENT_COL_WIDTH,
  InstrumentIdentity,
  NumberText,
  TRIPLET_WIDTH,
} from "@/shared/ui";
import { useMarketPolling } from "../../hooks/useMarketPolling";
import { fmtWanGu, fmtWanYi } from "../format";

/** "余量列"（营业部，不声明 width）的下限：宽屏吸收剩余宽度，窄屏不许低于此值 */
const BROKER_MIN_WIDTH = 180;
const MIN_TABLE_WIDTH =
  INSTRUMENT_COL_WIDTH.identity +
  TRIPLET_WIDTH.price +
  INSTRUMENT_COL_WIDTH.extra * 2 +
  BROKER_MIN_WIDTH * 2;

/**
 * 大宗交易清单（P1-2）。
 *
 * ⚠️ **这不是"没有三元组"，而是这张清单的载荷里根本没有涨跌口径**：`BlockTradeOut`
 * 只有 `price`（成交价，元）/ `volume`（万股）/ `amount`（万元）/ `buyer` / `seller`，
 * **既没有 `pct_change` 也没有 `change`**——大宗是"某笔成交"，不是"当日行情"。
 * 硬塞两个恒为 `--` 的涨跌列只是噪音，故这里只做**几何统一**：
 * 成交价沿用三元组的价格列宽（72px，`TRIPLET_WIDTH.price`），与其他清单的「最新价」
 * 同宽同位；标识格与它们同形（名称 + 小号代码）；数字一律 `.num` + 右对齐；
 * 缺失一律 `--`（不回落到 0）。要让它也有涨跌口径，需要后端补 `change` / `pct_change`
 * （单独决策，见 task-31-report 的"载荷比对"一节）。
 */
export function BlockTradeTable() {
  const navigate = useNavigate();
  const { refetchInterval } = useMarketPolling();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market", "block-trades"],
    queryFn: () => fetchBlockTrades(undefined, 15),
    staleTime: 5 * 60 * 1000,
    refetchInterval,
  });

  const columns: ColumnsType<BlockTradeItem> = [
    {
      className: "inst-cell--identity",
      title: "名称",
      dataIndex: "name",
      width: INSTRUMENT_COL_WIDTH.identity,
      render: (_, r) => <InstrumentIdentity symbol={r.symbol} name={r.name ?? "—"} />,
    },
    {
      title: "成交价", dataIndex: "price", width: TRIPLET_WIDTH.price, align: "right",
      render: (_, r) => <NumberText value={r.price} />,
    },
    {
      title: "成交量", dataIndex: "volume", width: INSTRUMENT_COL_WIDTH.extra, align: "right",
      render: (_, r) => <span className="num">{fmtWanGu(r.volume)}</span>,
    },
    {
      title: "成交额", dataIndex: "amount", width: INSTRUMENT_COL_WIDTH.extra, align: "right",
      render: (_, r) => <span className="num" style={{ fontWeight: 600 }}>{fmtWanYi(r.amount)}</span>,
    },
    { title: "买方营业部", dataIndex: "buyer", className: "inst-cell--flex", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.buyer ?? "—"}</Typography.Text> },
    { title: "卖方营业部", dataIndex: "seller", className: "inst-cell--flex", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.seller ?? "—"}</Typography.Text> },
  ];

  return (
    <Table<BlockTradeItem>
      rowKey={(r) => `${r.tsCode}-${r.buyer}-${r.price}-${r.volume}`}
      size="small"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      // 固定表布局 + `scroll.x`：声明了 width 的列**逐列精确**，唯一没声明 width 的
      // "余量列"吸收宽屏剩余空间、窄屏守住下限（见 MIN_TABLE_WIDTH 注释）。
      tableLayout="fixed"
      pagination={false}
      scroll={{ x: MIN_TABLE_WIDTH, y: 320 }}
      locale={{ emptyText: "暂无大宗交易数据" }}
      onRow={(r) => ({ onClick: () => navigate(`/company/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
