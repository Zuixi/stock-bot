import { Alert, Button, Card, Segmented, Table, Typography } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useRef, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import type {
  AnnouncementItem, BlockTradeItem, DragonTigerItem, RepurchaseItem, ShareFloatItem,
} from "@/shared/api/marketData";
import { useTheme } from "@/app/theme-context";
import { fmtSignedYi, fmtWanGu, fmtYi, fmtYiGu, fmtWanYi } from "@/features/market/components/format";
import { UnfilteredResultError, type EventTab, type StockEventsResult } from "../hooks/useStockEvents";

const KIND_OPTIONS: { label: string; value: EventTab }[] = [
  { label: "公告", value: "announcements" },
  { label: "龙虎榜", value: "dragon-tiger" },
  { label: "大宗", value: "block-trades" },
  { label: "解禁", value: "share-floats" },
  { label: "回购", value: "repurchases" },
];

/** 一行 → 可定位到 K 线的日期（与 `eventMarkers` 用的日期串同形，`YYYY-MM-DD`）。 */
const dateOf = {
  announcement: (r: AnnouncementItem) => r.announceTime.slice(0, 10),
  dragon: (r: DragonTigerItem) => r.tradeDate,
  block: (r: BlockTradeItem) => r.tradeDate,
  float: (r: ShareFloatItem) => r.floatDate,
  repo: (r: RepurchaseItem) => r.annDate,
};

/** 错误文案按错误种类分开：网络/HTTP 失败 ≠ 服务端没按标的上送过滤。 */
function errorCopy(error: unknown): { message: string; description: string } {
  if (error instanceof UnfilteredResultError) {
    return {
      message: "返回结果未按本股过滤",
      description: "接口返回了数据，但里面没有本股记录；这不等于本股没上榜。请重试。",
    };
  }
  return {
    message: "加载失败",
    description: "本次请求未能完成，不代表没有记录。请重试。",
  };
}

/**
 * 五个 tab 共用的三态渲染器：**失败 ≠ 无记录**。
 *
 * 旧实现统一写 `dataSource={q.data ?? []} + loading={q.isLoading}`，请求 500/断网时
 * `data` 仍是 `undefined`，表格落到 `locale.emptyText`，页面于是对用户宣称
 * 「暂无相关公告」——把一次失败伪装成一条事实。这里把三态显式分开：
 * - 错误（含重试在途）→ 行内错误块 + 「重试」（调本 query 的 `refetch()`），**绝不复用** `emptyText`；
 * - 成功且为空 → 原有 `emptyText`（暂无…），语义不变；
 * - 其余（loading / 有数据）→ 表格，loading 仍交给 Table 的骨架。
 *
 * 行点击（P1-2）：给了 `dateOf` + `onSelectDate` 时整行可点，把该行日期交给上层去定位
 * K 线；公告行里的标题仍是普通 `<a>`（外链 PDF 不变），点链接会连带触发行点击 ——
 * 两件事互不冲突（打开原文 + 图上定位到公告日）。
 */
function EventsTable<T extends object>({
  query, emptyText, columns, rowKey, dateOf, onSelectDate,
}: {
  query: UseQueryResult<T[]>;
  emptyText: string;
  columns: ColumnsType<T>;
  rowKey: string | ((record: T) => string);
  dateOf?: (record: T) => string;
  onSelectDate?: (date: string) => void;
}) {
  /**
   * 重试在途时**保留**错误块。React Query v5 在 refetch 开始时就把 `error` 清成 `null`、
   * `status` 回落 `pending`（`fetchState()`），只看 `isError` 的话点「重试」会立刻闪成一张
   * 空白表格 —— 恰好又回到「看起来像没有记录」。因此用本地 `retrying` 撑住错误态，
   * 并让「重试」在途时 loading + 真禁用（双击不会重复打接口）。
   */
  const [retrying, setRetrying] = useState(false);
  const lastErrorRef = useRef<unknown>(null);
  if (query.isError) lastErrorRef.current = query.error;

  const handleRetry = async () => {
    setRetrying(true);
    try {
      await query.refetch();
    } finally {
      setRetrying(false);
    }
  };

  if (query.isError || retrying) {
    // refetch 在途时 query.error 已被清空，用最近一次的 error 保住文案（别把「没按本股过滤」变成「加载失败」）
    const { message, description } = errorCopy(query.error ?? lastErrorRef.current);
    return (
      <div data-testid="related-events-error">
        <Alert
          type="error"
          showIcon
          message={message}
          description={description}
          action={
            <Button
              size="small"
              loading={retrying}
              disabled={retrying}
              data-testid="related-events-retry"
              onClick={() => void handleRetry()}
            >
              重试
            </Button>
          }
        />
      </div>
    );
  }
  const clickable = Boolean(dateOf && onSelectDate);
  return (
    <Table<T>
      rowKey={rowKey} size="small" loading={query.isLoading} pagination={false}
      dataSource={query.data ?? []} columns={columns} locale={{ emptyText }}
      onRow={clickable ? (record) => ({
        onClick: () => onSelectDate?.(dateOf?.(record) ?? ""),
        style: { cursor: "pointer" },
      }) : undefined}
    />
  );
}

export interface RelatedEventsProps {
  /** `useStockEvents` 的返回值（页面与 K 线标记共用同一份查询，见该 hook 的注释） */
  events: StockEventsResult;
  /** 当前 tab 由页面持有：K 线标记需要知道「哪一类已经加载」 */
  kind: EventTab;
  onKindChange: (kind: EventTab) => void;
  /** 点击某行 → 请求把 K 线定位到该行日期；日期不在已加载区间时由上层 no-op */
  onSelectDate?: (date: string) => void;
}

export function RelatedEvents({ events, kind, onKindChange, onSelectDate }: RelatedEventsProps) {
  const { colors } = useTheme();
  const { announcement: ann, dragon, block, floats, repo } = events;

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
        <Segmented size="small" value={kind} onChange={(v) => onKindChange(v as EventTab)} options={KIND_OPTIONS} />
      }
    >
      {kind === "announcements" && (
        <EventsTable<AnnouncementItem>
          query={ann} rowKey="announcementId" columns={annCols} emptyText="暂无相关公告"
          dateOf={dateOf.announcement} onSelectDate={onSelectDate}
        />
      )}
      {kind === "dragon-tiger" && (
        <EventsTable<DragonTigerItem>
          query={dragon} rowKey={(r) => `${r.tradeDate}-${r.reason}`} columns={dragonCols} emptyText="暂无上榜记录"
          dateOf={dateOf.dragon} onSelectDate={onSelectDate}
        />
      )}
      {kind === "block-trades" && (
        <EventsTable<BlockTradeItem>
          query={block} rowKey={(r) => `${r.tradeDate}-${r.buyer}-${r.price}-${r.volume}`} columns={blockCols}
          emptyText="暂无大宗交易记录" dateOf={dateOf.block} onSelectDate={onSelectDate}
        />
      )}
      {kind === "share-floats" && (
        <EventsTable<ShareFloatItem>
          query={floats} rowKey={(r) => `${r.floatDate}-${r.shareType}-${r.holderName}`} columns={floatCols}
          emptyText="暂无解禁记录" dateOf={dateOf.float} onSelectDate={onSelectDate}
        />
      )}
      {kind === "repurchases" && (
        <EventsTable<RepurchaseItem>
          query={repo} rowKey={(r) => `${r.annDate}-${r.proc}`} columns={repoCols} emptyText="暂无回购记录"
          dateOf={dateOf.repo} onSelectDate={onSelectDate}
        />
      )}
      <Typography.Text type="secondary" style={{ fontSize: 11, display: "block", marginTop: 4 }}>
        龙虎榜按本股代码查询，最多展示最近 50 条；点击任一行可在 K 线上定位到该日期
      </Typography.Text>
    </Card>
  );
}
