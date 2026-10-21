import { Table, Button, Empty, Tag, Tooltip } from "antd";
import { DeleteOutlined, ReloadOutlined } from "@ant-design/icons";
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
const ACTION_WIDTH = 56;
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
 * 一行 = 一个自选 symbol + 它可能取不到的行情材料。
 *
 * **为什么不直接过滤掉取不到的行**（2026-10-19 修）：旧实现 `Promise.all(...).filter(Boolean)`
 * 把"enrichment 取不到"的标的**静默丢掉** —— 标题说「我的自选（2）」、表里只有 1 行，
 * 而且丢掉的那一行**没有删除按钮**（按钮长在行里）。用户于是看到"自选加了不显示 / 不能删"。
 * 现在每一只票都有一行：取不到就显示 `--` + 「行情未取到」，删除入口始终在。
 */
interface WatchlistRow {
  symbol: string;
  stock: StockRecord | null;
}

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

  const {
    data = [],
    isLoading,
    isFetching,
    refetch,
  } = useQuery<WatchlistRow[]>({
    queryKey: ["watchlist-stocks", items],
    queryFn: async () => {
      // **allSettled，不是 all**：一只票取不到不该让整张表消失（旧实现就是整表空）
      const settled = await Promise.allSettled(
        items.map((symbol) => fetchStockEnrichedBySymbol(symbol))
      );
      return items.map((symbol, idx) => {
        const result = settled[idx];
        return {
          symbol,
          stock: result?.status === "fulfilled" ? result.value : null,
        };
      });
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

  const unresolved = data.filter((row) => row.stock == null);

  const columns: ColumnsType<WatchlistRow> = [
    {
      // 余量列：**不声明 width**（见 MIN_TABLE_WIDTH 的注释）。给这一列加 width 会让
      // 宽屏下的余量按比例摊派、三元组列宽随之漂移 —— e2e 钉住了 72/62/66。
      //
      // ⚠️ **每列必须有唯一 `key`**（2026-10-19 修）：antd 的列标识是 `key ?? dataIndex`。
      // 本轮把行类型改成 `{symbol, stock}` 后，所有列都写成 `dataIndex: "symbol"` ⇒ 六列
      // 共用同一个列 key，`useSorter` 的排序状态也共用一个 key：点「最新价」会让
      // 最新价/涨跌额/涨跌幅/总市值**四列同时出现升序箭头**，且再点别的列只是在同一个 key
      // 上切换。排序函数本身是对的，坏的是列的**身份**。
      key: "name",
      className: "inst-cell--identity",
      title: "名称",
      render: (_: string, row: WatchlistRow) =>
        row.stock ? (
          <InstrumentIdentity
            symbol={row.stock.symbol}
            name={
              <a onClick={() => navigate(`/company/${row.stock!.symbol}`)}>{row.stock.name}</a>
            }
          />
        ) : (
          <span className="inst-cell__unresolved" data-testid="watchlist-unresolved">
            <span className="num">{row.symbol}</span>
            <Tag color="default" bordered={false}>
              行情未取到
            </Tag>
          </span>
        ),
    },
    {
      key: "price",
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.price,
      width: TRIPLET_WIDTH.price,
      sorter: (a: WatchlistRow, b: WatchlistRow) =>
        (a.stock?.latestPrice ?? 0) - (b.stock?.latestPrice ?? 0),
      align: "right" as const,
      render: (_: string, row: WatchlistRow) => <NumberText value={row.stock?.latestPrice} />,
    },
    {
      key: "change",
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.change,
      width: TRIPLET_WIDTH.change,
      sorter: (a: WatchlistRow, b: WatchlistRow) =>
        (a.stock?.change ?? 0) - (b.stock?.change ?? 0),
      align: "right" as const,
      render: (_: string, row: WatchlistRow) => <DeltaAbsText value={row.stock?.change} />,
    },
    {
      key: "pct",
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.pct,
      width: TRIPLET_WIDTH.pct,
      sorter: (a: WatchlistRow, b: WatchlistRow) =>
        (a.stock?.changePercent ?? 0) - (b.stock?.changePercent ?? 0),
      align: "right" as const,
      render: (_: string, row: WatchlistRow) => <ChangeText value={row.stock?.changePercent} />,
    },
    {
      key: "exchange",
      title: "交易所",
      width: EXCHANGE_WIDTH,
      render: (_: string, row: WatchlistRow) =>
        row.stock
          ? (EXCHANGE_LABELS[row.stock.exchange as keyof typeof EXCHANGE_LABELS] ??
            row.stock.exchange)
          : "--",
    },
    {
      key: "market-cap",
      title: "总市值",
      width: INSTRUMENT_COL_WIDTH.extra,
      sorter: (a: WatchlistRow, b: WatchlistRow) =>
        (a.stock?.marketCap ?? 0) - (b.stock?.marketCap ?? 0),
      align: "right" as const,
      render: (_: string, row: WatchlistRow) => <NumberText value={row.stock?.marketCap} unit="cap" />,
    },
    {
      title: "",
      key: "action",
      width: ACTION_WIDTH,
      render: (_: unknown, row: WatchlistRow) => (
        <Tooltip title="移除自选">
          <Button
            type="text"
            size="small"
            danger
            loading={isMutating}
            // 图标按钮必须有可访问名称：Tooltip 不是 accessible name，
            // 屏幕阅读器与 e2e 的 getByRole(name) 都读不到（2026-10-19）。
            aria-label={`移除自选 ${row.symbol}`}
            icon={<DeleteOutlined />}
            onClick={(e) => {
              e.stopPropagation();
              remove(row.symbol);
            }}
          />
        </Tooltip>
      ),
    },
  ];

  return (
    <>
      {unresolved.length > 0 ? (
        <p className="watchlist__unresolved-hint" data-testid="watchlist-unresolved-hint">
          有 {unresolved.length} 只自选的行情暂未取到（行仍在，可直接移除）。
          <Button
            type="link"
            size="small"
            icon={<ReloadOutlined />}
            loading={isFetching}
            onClick={() => void refetch()}
          >
            重试
          </Button>
        </p>
      ) : null}
      <Table<WatchlistRow>
        columns={columns}
        dataSource={data}
        tableLayout="fixed"
        loading={isLoading}
        rowKey="symbol"
        size="small"
        pagination={false}
        scroll={{ x: MIN_TABLE_WIDTH }}
        onRow={(row) => ({
          style: { cursor: row.stock ? "pointer" : "default" },
          onClick: () => {
            if (row.stock) navigate(`/company/${row.stock.symbol}`);
          },
        })}
      />
    </>
  );
}
