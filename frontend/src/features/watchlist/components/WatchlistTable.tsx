import { Table, Button, Empty, Tooltip } from "antd";
import { DeleteOutlined } from "@ant-design/icons";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ChangeText,
  DeltaAbsText,
  INSTRUMENT_COL_WIDTH,
  InstrumentIdentity,
  NumberText,
  TRIPLET_COLUMNS_WIDTH,
  TRIPLET_LABEL,
  TRIPLET_WIDTH,
} from "@/shared/ui";
import { useWatchlist } from "../useWatchlist";
import { fetchStockEnrichedBySymbol } from "@/shared/api/stocks";
import { EXCHANGE_LABELS } from "@/shared/types";
import type { StockRecord } from "@/shared/types";
import type { ColumnsType } from "antd/es/table";

const EXCHANGE_WIDTH = 72;
const ACTION_WIDTH = 48;
/**
 * `scroll.x`：列宽之和（含"余量列"的下限 110）。两个作用：
 * - **窄屏**：表格宽度锁定在这个值 → 由 antd 自带容器横向滚动（表头/表体同步），
 *   列不会被压到 0 宽（改造前 390 下后缀列实测就是 0px）；
 * - **宽屏**：`scroll.x` 会给表格打上 `min-width: 100%`，表格按容器宽度铺开；
 *   此时**唯一没声明 `width` 的"余量列"**吸收全部剩余宽度，其余列（含三元组）
 *   保持声明值逐列精确（配合 `tableLayout="fixed"`：实测 1440 下 72/62/66 逐列命中；
 *   若每列都声明了宽度，浏览器会把余量按比例摊给每一列，实测最新价被摊成 187）。
 */
const MIN_TABLE_WIDTH =
  INSTRUMENT_COL_WIDTH.identity +
  TRIPLET_COLUMNS_WIDTH +
  EXCHANGE_WIDTH +
  INSTRUMENT_COL_WIDTH.extra +
  ACTION_WIDTH;

/**
 * 自选清单：**本仓库里唯一载荷齐了指标三元组三个成员的标的清单**
 * （`StockEnrichedOut` 同时回 `latest_price` / `change` / `change_percent`），
 * 故三元组在这里是三个真值，不是占位。
 *
 * P1-2：列序统一为 `标识 → 最新价 → 涨跌额 → 涨跌幅 → 交易所 → 总市值 → 操作`，
 * 列宽全部走 `instrumentTriplet.ts` 的共享常量（72 / 62 / 66），与 `/market` 的龙虎榜、
 * `/` 的榜单同列宽——眼睛在几张清单之间移动时不必重新找列。数值格一律 `.num`
 * （tabular figures）+ 右对齐；缺失一律 `--`，**绝不回落 0**。
 * 标识格是"余量列"（不声明 width）：宽屏吸收卡片剩余宽度，窄屏守住 110px，
 * 使 `标识 110 + 三元组 200 = 310` 在 390 视口（表格内容宽 316–342）内完整可见。
 */
export function WatchlistTable() {
  const navigate = useNavigate();
  const { items, remove, isMutating } = useWatchlist();

  const { data = [], isLoading } = useQuery({
    queryKey: ["watchlist-stocks", items],
    queryFn: async () => {
      const rows = await Promise.all(items.map((symbol) => fetchStockEnrichedBySymbol(symbol)));
      return rows.filter((stock): stock is StockRecord => Boolean(stock));
    },
    enabled: items.length > 0,
  });

  if (!isLoading && items.length === 0) {
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
      // 余量列：**不声明 width**（见 MIN_TABLE_WIDTH 的注释）。给这一列加 width 会让
      // 宽屏下的余量按比例摊派、三元组列宽随之漂移 —— e2e 钉住了 72/62/66。
      className: "inst-cell--identity",
      title: "名称",
      dataIndex: "name",
      render: (v: string, r: StockRecord) => (
        <InstrumentIdentity
          symbol={r.symbol}
          name={<a onClick={() => navigate(`/company/${r.symbol}`)}>{v}</a>}
        />
      ),
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.price,
      dataIndex: "latestPrice",
      width: TRIPLET_WIDTH.price,
      sorter: (a: StockRecord, b: StockRecord) => (a.latestPrice ?? 0) - (b.latestPrice ?? 0),
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} />,
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.change,
      dataIndex: "change",
      width: TRIPLET_WIDTH.change,
      sorter: (a: StockRecord, b: StockRecord) => (a.change ?? 0) - (b.change ?? 0),
      align: "right" as const,
      render: (v: number | undefined) => <DeltaAbsText value={v} />,
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.pct,
      dataIndex: "changePercent",
      width: TRIPLET_WIDTH.pct,
      sorter: (a: StockRecord, b: StockRecord) => (a.changePercent ?? 0) - (b.changePercent ?? 0),
      align: "right" as const,
      render: (v: number | undefined) => <ChangeText value={v} />,
    },
    {
      title: "交易所",
      dataIndex: "exchange",
      width: EXCHANGE_WIDTH,
      render: (v: string) => EXCHANGE_LABELS[v as keyof typeof EXCHANGE_LABELS] ?? v,
    },
    {
      title: "总市值",
      dataIndex: "marketCap",
      width: INSTRUMENT_COL_WIDTH.extra,
      sorter: (a: StockRecord, b: StockRecord) => (a.marketCap ?? 0) - (b.marketCap ?? 0),
      align: "right" as const,
      render: (v: number | undefined) => <NumberText value={v} unit="cap" />,
    },
    {
      title: "",
      key: "action",
      width: ACTION_WIDTH,
      render: (_: unknown, record: StockRecord) => (
        <Tooltip title="移除自选">
          <Button
            type="text"
            size="small"
            danger
            loading={isMutating}
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
      tableLayout="fixed"
      loading={isLoading}
      rowKey="symbol"
      size="small"
      pagination={false}
      scroll={{ x: MIN_TABLE_WIDTH }}
      onRow={(record) => ({
        style: { cursor: "pointer" },
        onClick: () => navigate(`/company/${record.symbol}`),
      })}
    />
  );
}
