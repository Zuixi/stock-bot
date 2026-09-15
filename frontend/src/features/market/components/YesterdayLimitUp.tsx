import { Table, Tag, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { ChangeText } from "@/shared/ui";
import { formatCnDate } from "@/shared/ui/date";
import type { YesterdayLimitUp, YesterdayLimitUpItem } from "@/shared/api/limitUp";

interface Props {
  data: YesterdayLimitUp | null | undefined;
  /** 端点降级（如 partial_day）时渲染短占位文案，不展示不完整数据。 */
  degraded?: boolean;
}

/** 状态 Tag：晋级（isLu）/ 炸板（broken）/ 停牌（suspended）/ 震荡。停牌优先于晋级判断。 */
function statusTag(item: YesterdayLimitUpItem) {
  if (item.suspended) return <Tag>停牌</Tag>;
  if (item.isLu) return <Tag color="red">晋级</Tag>;
  if (item.broken) return <Tag color="orange">炸板</Tag>;
  return <Tag>震荡</Tag>;
}

/**
 * 昨日涨停今日表现：`todayPct == null`（含停牌）渲染 `--`，绝不回落 0.00%。
 */
export function YesterdayLimitUp({ data, degraded = false }: Props) {
  const columns: ColumnsType<YesterdayLimitUpItem> = [
    {
      title: "代码",
      dataIndex: "symbol",
      width: 84,
      render: (_, r) => <span style={{ fontVariantNumeric: "tabular-nums" }}>{r.symbol}</span>,
    },
    { title: "名称", dataIndex: "name", width: 90 },
    {
      title: "细分行业",
      dataIndex: "swL3Name",
      ellipsis: true,
      render: (_, r) => r.swL3Name ?? "--",
    },
    {
      title: "昨日连板",
      dataIndex: "prevStreak",
      align: "right",
      width: 84,
      render: (_, r) => `${r.prevStreak}板`,
    },
    {
      title: "今日今开溢价",
      dataIndex: "todayOpenPremium",
      align: "right",
      width: 110,
      render: (_, r) => <ChangeText value={r.todayOpenPremium} />,
    },
    {
      title: "今日涨幅",
      dataIndex: "todayPct",
      align: "right",
      width: 90,
      render: (_, r) => <ChangeText value={r.todayPct} />,
    },
    {
      title: "状态",
      key: "status",
      width: 72,
      render: (_, r) => statusTag(r),
    },
  ];

  if (degraded) {
    return (
      <Typography.Text
        type="secondary"
        style={{ display: "block", padding: "24px 0", textAlign: "center" }}
      >
        数据不完整，暂不展示昨日表现
      </Typography.Text>
    );
  }

  return (
    <div>
      {/* 口径说明：卡头「数据截至」是表现日；涨停日（as_of_prev）在这里显式标注，
          否则会被误读为「数据落后一个交易日」（实测 2026-09-15 用户反馈）。 */}
      {data?.asOf && data?.asOfPrev ? (
        <div style={{ fontSize: 12, color: "var(--text-secondary)", marginBottom: 8 }}>
          统计 {formatCnDate(data.asOfPrev)} 涨停股在 {formatCnDate(data.asOf)} 的表现 · 行情 T+1
          回补，当日表现次日生成
        </div>
      ) : null}
      <Table<YesterdayLimitUpItem>
        rowKey="symbol"
        size="small"
        columns={columns}
        dataSource={data?.items ?? []}
        pagination={false}
        scroll={{ x: 700 }}
        locale={{ emptyText: "无昨日涨停样本" }}
      />
    </div>
  );
}
