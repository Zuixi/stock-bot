import { Card, Segmented, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  fetchAnnouncements, fetchBlockTrades, fetchDragonTiger,
  fetchRepurchases, fetchShareFloats,
  type AnnouncementItem, type BlockTradeItem, type DragonTigerItem,
  type RepurchaseItem, type ShareFloatItem,
} from "@/shared/api/marketData";
import { COLORS } from "@/app/theme";
import { fmtSignedYi, fmtWanGu, fmtYi, fmtYiGu, fmtWanYi } from "@/features/market/components/format";

const STALE_TIME = 5 * 60 * 1000;
const NUM_FONT: React.CSSProperties = { fontVariantNumeric: "tabular-nums" };

type Kind = "announcements" | "dragon-tiger" | "block-trades" | "share-floats" | "repurchases";

const KIND_OPTIONS = [
  { label: "公告", value: "announcements" },
  { label: "龙虎榜", value: "dragon-tiger" },
  { label: "大宗", value: "block-trades" },
  { label: "解禁", value: "share-floats" },
  { label: "回购", value: "repurchases" },
] as const;

export function RelatedEvents({ symbol }: { symbol: string }) {
  const [kind, setKind] = useState<Kind>("announcements");

  const ann = useQuery({
    queryKey: ["related-events", symbol, "announcements"],
    queryFn: () => fetchAnnouncements(symbol, 10),
    staleTime: STALE_TIME, enabled: kind === "announcements",
  });
  const dragon = useQuery({
    queryKey: ["related-events", symbol, "dragon-tiger"],
    queryFn: () => fetchDragonTiger(50).then((rows) => rows.filter((r) => r.symbol === symbol)),
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

  const dragonCols: ColumnsType<DragonTigerItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "净买额", dataIndex: "netAmount", width: 100, align: "right",
      render: (_, r) => <span style={{ ...NUM_FONT, color: (r.netAmount ?? 0) > 0 ? COLORS.up : COLORS.down }}>{fmtSignedYi(r.netAmount)}</span> },
    { title: "上榜原因", dataIndex: "reason", ellipsis: true, render: (_, r) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{r.reason}</Typography.Text> },
  ];
  const blockCols: ColumnsType<BlockTradeItem> = [
    { title: "日期", dataIndex: "tradeDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "成交价", dataIndex: "price", width: 80, align: "right", render: (v) => <span style={NUM_FONT}>{v?.toFixed(2) ?? "—"}</span> },
    { title: "成交量", dataIndex: "volume", width: 90, align: "right", render: (v) => fmtWanGu(v) },
    { title: "成交额", dataIndex: "amount", width: 90, align: "right", render: (v) => fmtWanYi(v) },
    { title: "买方", dataIndex: "buyer", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const floatCols: ColumnsType<ShareFloatItem> = [
    { title: "解禁日", dataIndex: "floatDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
    { title: "数量", dataIndex: "floatShare", width: 90, align: "right", render: (v) => fmtYiGu(v) },
    { title: "占比", dataIndex: "floatRatio", width: 70, align: "right", render: (v) => (v == null ? "—" : `${v.toFixed(2)}%`) },
    { title: "类型", dataIndex: "shareType", ellipsis: true, render: (v) => <Typography.Text type="secondary" style={{ fontSize: 12 }}>{v ?? "—"}</Typography.Text> },
  ];
  const repoCols: ColumnsType<RepurchaseItem> = [
    { title: "公告日", dataIndex: "annDate", width: 100, render: (v: string) => <span style={NUM_FONT}>{v}</span> },
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
        <Table<AnnouncementItem>
          rowKey="announcementId" size="small" loading={ann.isLoading} pagination={false}
          dataSource={ann.data ?? []} locale={{ emptyText: "暂无相关公告" }}
          columns={[
            { title: "时间", dataIndex: "announceTime", width: 130, render: (v: string) => <span style={NUM_FONT}>{v.slice(0, 16).replace("T", " ")}</span> },
            { title: "标题", dataIndex: "title", ellipsis: true,
              render: (_, r) => <a href={r.pdfUrl ?? undefined} target="_blank" rel="noreferrer">{r.title}</a> },
          ]}
        />
      )}
      {kind === "dragon-tiger" && (
        <Table<DragonTigerItem> rowKey={(r) => `${r.tradeDate}-${r.reason}`} size="small" loading={dragon.isLoading}
          pagination={false} dataSource={dragon.data ?? []} columns={dragonCols} locale={{ emptyText: "暂无上榜记录" }} />
      )}
      {kind === "block-trades" && (
        <Table<BlockTradeItem> rowKey={(r) => `${r.tradeDate}-${r.buyer}-${r.price}-${r.volume}`} size="small" loading={block.isLoading}
          pagination={false} dataSource={block.data ?? []} columns={blockCols} locale={{ emptyText: "暂无大宗交易记录" }} />
      )}
      {kind === "share-floats" && (
        <Table<ShareFloatItem> rowKey={(r) => `${r.floatDate}-${r.shareType}-${r.holderName}`} size="small" loading={floats.isLoading}
          pagination={false} dataSource={floats.data ?? []} columns={floatCols} locale={{ emptyText: "暂无解禁记录" }} />
      )}
      {kind === "repurchases" && (
        <Table<RepurchaseItem> rowKey={(r) => `${r.annDate}-${r.proc}`} size="small" loading={repo.isLoading}
          pagination={false} dataSource={repo.data ?? []} columns={repoCols} locale={{ emptyText: "暂无回购记录" }} />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
        数据源：TuShare / 巨潮资讯 · 龙虎榜为全市场最新日筛选本股
      </Typography.Text>
    </Card>
  );
}
