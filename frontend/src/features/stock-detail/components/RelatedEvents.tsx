import { Alert, Button, Card, Segmented, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  fetchAnnouncements, fetchBlockTrades, fetchDragonTiger,
  fetchRepurchases, fetchShareFloats,
  type AnnouncementItem, type BlockTradeItem, type DragonTigerItem,
  type RepurchaseItem, type ShareFloatItem,
} from "@/shared/api/marketData";
import { useTheme } from "@/app/theme-context";
import { fmtSignedYi, fmtWanGu, fmtYi, fmtYiGu, fmtWanYi } from "@/features/market/components/format";

const STALE_TIME = 5 * 60 * 1000;
type Kind = "announcements" | "dragon-tiger" | "block-trades" | "share-floats" | "repurchases";

const KIND_OPTIONS = [
  { label: "公告", value: "announcements" },
  { label: "龙虎榜", value: "dragon-tiger" },
  { label: "大宗", value: "block-trades" },
  { label: "解禁", value: "share-floats" },
  { label: "回购", value: "repurchases" },
] as const;

/**
 * 五个 tab 共用的三态渲染器：**失败 ≠ 无记录**。
 *
 * 旧实现统一写 `dataSource={q.data ?? []} + loading={q.isLoading}`，请求 500/断网时
 * `data` 仍是 `undefined`，表格落到 `locale.emptyText`，页面于是对用户宣称
 * 「暂无相关公告」——把一次失败伪装成一条事实。这里把三态显式分开：
 * - `isError` → 行内错误块 + 「重试」（调本 query 的 `refetch()`），**绝不复用** `emptyText`；
 * - 成功且为空 → 原有 `emptyText`（暂无…），语义不变；
 * - 其余（loading / 有数据）→ 表格，loading 仍交给 Table 的骨架。
 */
function EventsTable<T extends object>({
  query, emptyText, columns, rowKey,
}: {
  query: UseQueryResult<T[]>;
  emptyText: string;
  columns: ColumnsType<T>;
  rowKey: string | ((record: T) => string);
}) {
  if (query.isError) {
    return (
      <div data-testid="related-events-error">
        <Alert
          type="error"
          showIcon
          message="加载失败"
          description="本次请求未能完成，不代表没有记录。请重试。"
          action={
            <Button size="small" data-testid="related-events-retry" onClick={() => void query.refetch()}>
              重试
            </Button>
          }
        />
      </div>
    );
  }
  return (
    <Table<T>
      rowKey={rowKey} size="small" loading={query.isLoading} pagination={false}
      dataSource={query.data ?? []} columns={columns} locale={{ emptyText }}
    />
  );
}

export function RelatedEvents({ symbol }: { symbol: string }) {
  const { colors } = useTheme();
  const [kind, setKind] = useState<Kind>("announcements");

  const ann = useQuery({
    queryKey: ["related-events", symbol, "announcements"],
    queryFn: () => fetchAnnouncements(symbol, 10),
    staleTime: STALE_TIME, enabled: kind === "announcements",
  });
  const dragon = useQuery({
    queryKey: ["related-events", symbol, "dragon-tiger"],
    // symbol 交给服务端过滤（该股跨交易日）；客户端 filter 只作兜底护栏，不再是唯一过滤手段
    queryFn: () => fetchDragonTiger(50, symbol).then((rows) => rows.filter((r) => r.symbol === symbol)),
    staleTime: STALE_TIME, enabled: kind === "dragon-tiger",
  });
  const block = useQuery({
    queryKey: ["related-events", symbol, "block-trades"],
    queryFn: () => fetchBlockTrades(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "block-trades",
  });
  const floats = useQuery({
    queryKey: ["related-events", symbol, "share-floats"],
    queryFn: () => fetchShareFloats(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "share-floats",
  });
  const repo = useQuery({
    queryKey: ["related-events", symbol, "repurchases"],
    queryFn: () => fetchRepurchases(symbol, 15),
    staleTime: STALE_TIME, enabled: kind === "repurchases",
  });

  const annCols: ColumnsType<AnnouncementItem> = [
    { title: "时间", dataIndex: "announceTime", width: 130, render: (v: string) => <span className="num">{v.slice(0, 16).replace("T", " ")}</span> },
    { title: "标题", dataIndex: "title", ellipsis: true,
      render: (_, r) => <a href={r.pdfUrl ?? undefined} target="_blank" rel="noreferrer">{r.title}</a> },
  ];
  const dragonCols: ColumnsType<DragonTigerItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span className="num">{v}</span> },
    { title: "净买额", dataIndex: "netAmount", width: 100, align: "right",
      render: (_, r) => <span className="num" style={{ color: (r.netAmount ?? 0) > 0 ? colors.up : colors.down }}>{fmtSignedYi(r.netAmount)}</span> },
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];
  const blockCols: ColumnsType<BlockTradeItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span className="num">{v}</span> },
    { title: "成交价", dataIndex: "price", width: 80, align: "right", render: (v) => <span className="num">{v?.toFixed(2) ?? "—"}</span> },
    { title: "成交量", dataIndex: "volume", width: 90, align: "right", render: (v) => fmtWanGu(v) },
    { title: "成交额", dataIndex: "amount", width: 90, align: "right", render: (v) => fmtWanYi(v) },
    { title: "买方", dataIndex: "buyer", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const floatCols: ColumnsType<ShareFloatItem> = [
    { title: "解禁日", dataIndex: "floatDate", width: 100, render: (v: string) => <span className="num">{v}</span> },
    { title: "数量", dataIndex: "floatShare", width: 90, align: "right", render: (v) => fmtYiGu(v) },
    { title: "占比", dataIndex: "floatRatio", width: 70, align: "right", render: (v) => (v == null ? "—" : `${v.toFixed(2)}%`) },
    { title: "类型", dataIndex: "shareType", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const repoCols: ColumnsType<RepurchaseItem> = [
    { title: "公告日", dataIndex: "annDate", width: 100, render: (v: string) => <span className="num">{v}</span> },
    { title: "进度", dataIndex: "proc", width: 70, render: (v: string) => <Typography.Text>{v}</Typography.Text> },
    { title: "回购金额", dataIndex: "amount", width: 100, align: "right", render: (v) => fmtYi(v) },
  ];

  return (
    <Card
      title="相关数据"
      size="small"
      extra={
        <Segmented size="small" value={kind} onChange={(v) => setKind(v as Kind)} options={[...KIND_OPTIONS]} />
      }
    >
      {kind === "announcements" && (
        <EventsTable<AnnouncementItem>
          query={ann} rowKey="announcementId" columns={annCols} emptyText="暂无相关公告"
        />
      )}
      {kind === "dragon-tiger" && (
        <EventsTable<DragonTigerItem>
          query={dragon} rowKey={(r) => `${r.tradeDate}-${r.reason}`} columns={dragonCols} emptyText="暂无上榜记录"
        />
      )}
      {kind === "block-trades" && (
        <EventsTable<BlockTradeItem>
          query={block} rowKey={(r) => `${r.tradeDate}-${r.buyer}-${r.price}-${r.volume}`} columns={blockCols}
          emptyText="暂无大宗交易记录"
        />
      )}
      {kind === "share-floats" && (
        <EventsTable<ShareFloatItem>
          query={floats} rowKey={(r) => `${r.floatDate}-${r.shareType}-${r.holderName}`} columns={floatCols}
          emptyText="暂无解禁记录"
        />
      )}
      {kind === "repurchases" && (
        <EventsTable<RepurchaseItem>
          query={repo} rowKey={(r) => `${r.annDate}-${r.proc}`} columns={repoCols} emptyText="暂无回购记录"
        />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
        龙虎榜按本股代码向服务端请求（跨交易日），不再从前端全市场榜单里筛
      </Typography.Text>
    </Card>
  );
}
