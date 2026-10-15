import { Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { fetchDragonTiger, type DragonTigerItem } from "@/shared/api/marketData";
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
import { useTheme } from "@/app/theme-context";
import { useMarketPolling } from "../../hooks/useMarketPolling";
import { fmtSignedYi } from "../format";

const NET_AMOUNT_WIDTH = 110;
/** "余量列"（上榜原因，不声明 width）的下限：宽屏时它吸收剩余宽度，窄屏时不许低于此值。 */
const REASON_MIN_WIDTH = 180;
/** 见 `WatchlistTable` 的 MIN_TABLE_WIDTH 注释：`scroll.x` = 列宽之和（含余量列下限）。 */
const MIN_TABLE_WIDTH =
  INSTRUMENT_COL_WIDTH.identity + TRIPLET_COLUMNS_WIDTH + NET_AMOUNT_WIDTH + REASON_MIN_WIDTH;

/**
 * 龙虎榜清单（P1-2）：列序改成标的清单统一的**指标三元组**
 * `最新价 → 涨跌额 → 涨跌幅`（列宽取自 `instrumentTriplet.ts`，与自选/榜单同宽：
 * 实测 1440 下 72 / 62 / 66 逐列命中，与 landing 榜单行的栅格列完全一致）。
 *
 * 载荷比对（实测 `GET /api/v1/market/dragon-tiger`）：`DragonTigerOut` 只回
 * `close` / `pct_change`（表 `dragon_tiger` 本身也只有 `close` / `pct_change`，
 * **没有** `pre_close`）——于是「最新价」「涨跌幅」是真值（最新价此前根本没上屏，
 * 这次是净增的一个数），中间那格「涨跌额」**如实显示 `--`**：前端口径里没有这个数，
 * 绝不拿 `close × pct/(100+pct)` 反算一个看起来像真值的数（那是编造，且新股的参考价
 * 口径会让它错）。补齐它需要后端在 `/market/dragon-tiger` 回 `change` 或 `pre_close`。
 *
 * 窄屏（390）：`scroll.x` 让它在本卡内横向滚动（表头/表体同步，**列不压缩**）
 * ——改造前「上榜原因」在 390 实测宽度 = 0px，等于整列读不出来。整页不溢出。
 */
export function DragonTigerTable() {
  const navigate = useNavigate();
  const { colors } = useTheme();
  const { refetchInterval } = useMarketPolling();
  const { data = [], isLoading } = useQuery({
    queryKey: ["market", "dragon-tiger"],
    queryFn: () => fetchDragonTiger(15),
    staleTime: 5 * 60 * 1000,
    refetchInterval,
  });

  const columns: ColumnsType<DragonTigerItem> = [
    {
      className: "inst-cell--identity",
      title: "名称",
      dataIndex: "name",
      width: INSTRUMENT_COL_WIDTH.identity,
      render: (_, r) => <InstrumentIdentity symbol={r.symbol} name={r.name ?? "—"} />,
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.price, dataIndex: "close", width: TRIPLET_WIDTH.price, align: "right",
      render: (_, r) => <NumberText value={r.close} />,
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.change, dataIndex: "change", width: TRIPLET_WIDTH.change, align: "right",
      // 该端点不返回涨跌额：槽位占位 + `--`，绝不回落 0（见组件头注释）
      render: () => <DeltaAbsText value={undefined} />,
    },
    {
      className: "inst-cell--triplet",
      title: TRIPLET_LABEL.pct, dataIndex: "pctChange", width: TRIPLET_WIDTH.pct, align: "right",
      render: (_, r) => <ChangeText value={r.pctChange} />,
    },
    {
      title: "龙虎榜净买额", dataIndex: "netAmount", width: NET_AMOUNT_WIDTH, align: "right",
      render: (_, r) => (
        <span className="num" style={{ fontWeight: 600, color: (r.netAmount ?? 0) > 0 ? colors.up : (r.netAmount ?? 0) < 0 ? colors.down : colors.flat }}>
          {fmtSignedYi(r.netAmount)}
        </span>
      ),
    },
    // 余量列：**不声明 width**（给这一列加 width 会让宽屏下的余量按比例摊派，
    // 三元组列宽随之漂移 —— e2e 钉住了 72/62/66）。
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];

  return (
    <Table<DragonTigerItem>
      rowKey={(r) => `${r.tsCode}-${r.reason}`}
      size="small"
      tableLayout="fixed"
      loading={isLoading}
      columns={columns}
      dataSource={data}
      pagination={false}
      scroll={{ x: MIN_TABLE_WIDTH, y: 320 }}
      locale={{ emptyText: "暂无龙虎榜数据（盘后自动更新）" }}
      onRow={(r) => ({ onClick: () => navigate(`/company/${r.symbol}`), style: { cursor: "pointer" } })}
    />
  );
}
