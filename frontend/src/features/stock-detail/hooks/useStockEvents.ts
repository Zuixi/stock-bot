import { useMemo } from "react";
import { useQuery, type UseQueryResult } from "@tanstack/react-query";
import {
  fetchAnnouncements, fetchBlockTrades, fetchDragonTiger,
  fetchRepurchases, fetchShareFloats,
  type AnnouncementItem, type BlockTradeItem, type DragonTigerItem,
  type RepurchaseItem, type ShareFloatItem,
} from "@/shared/api/marketData";
import type { StockEvent } from "../lib/eventMarkers";

/**
 * 个股详情的**五个事件查询**，以及「把各行归一成 `StockEvent[]`」的派生。
 *
 * 为什么必须提成 hook（brief 非协商项）：K 线要打事件标记、列表要渲染同一批事件，
 * 如果各拉一份，同一个端点会被请求两次（且两份数据可能在不同时刻到达，图上/list 上
 * 看到的事件不是同一批）。这里让**页面与列表消费同一份查询结果**：查询键逐字沿用
 * `RelatedEvents` 原来的 `["related-events", symbol, <kind>]`，React Query 按 key 去重，
 * 页面拿到的 `events` 与列表行永远同源。
 *
 * **懒加载语义原样保留**（`enabled: activeKind === <kind>`）：`stockDetailEvents.spec.ts`
 * 有一条断言「打开详情页时还不该有龙虎榜请求」，并把这条当作「tab 用 enabled 懒加载」
 * 的回归网；这里不能为了"首屏就能画全部五类标记"而把它改成一律预取 —— 那会把请求数
 * 从 1 变成 5，也直接打红既有用例。因此图上的标记 = **已加载查询**的事件；用户点开
 * 某个 tab 后，该类的标记会在同一次查询返回时出现在图上（不多发一个请求）。
 */

export const STALE_TIME = 5 * 60 * 1000;

/** 「相关数据」卡的五个 tab（值即查询键里的后缀，沿用原有命名）。 */
export type EventTab = "announcements" | "dragon-tiger" | "block-trades" | "share-floats" | "repurchases";

/** 各类 tab 对应的 `EventKind`（`events` 归一化时用）。 */
const TAB_TO_KIND = {
  announcements: "announcement",
  "dragon-tiger": "dragon-tiger",
  "block-trades": "block-trade",
  "share-floats": "share-float",
  repurchases: "repurchase",
} as const;

/**
 * 「接口有返回，但没有本股的行」——这必须当**错误**，不能当空态。
 *
 * 唯一可能的成因是后端没按 `symbol` 过滤（= 旧的「全市场榜单 + 前端筛」形状）。
 * 若把它静默渲染成「暂无上榜记录」，用户看到的是「本股没上榜」这条**假事实**，
 * 与真的没有记录无从区分 —— 正是本任务修的那类谎言，只是下沉了一层。
 * 已知它现在会命中：运行中的后端可能还没部署 `symbol` 参数。
 */
export class UnfilteredResultError extends Error {
  constructor() {
    super("返回结果未按本股过滤");
    this.name = "UnfilteredResultError";
  }
}

export interface StockEventsResult {
  announcement: UseQueryResult<AnnouncementItem[]>;
  dragon: UseQueryResult<DragonTigerItem[]>;
  block: UseQueryResult<BlockTradeItem[]>;
  floats: UseQueryResult<ShareFloatItem[]>;
  repo: UseQueryResult<RepurchaseItem[]>;
  /**
   * 已加载查询的事件（未加载的 kind 不参与）。派生而非另取数：
   * K 线标记与列表行必须来自同一份响应，否则"图上有标记但列表没有"会自相矛盾。
   */
  events: StockEvent[];
}

/** 后端日期 → `YYYY-MM-DD`；形状不对就丢弃（宁可没有标记，也不画到错误的日子）。 */
function isoDate(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const date = raw.slice(0, 10);
  return /^\d{4}-\d{2}-\d{2}$/.test(date) ? date : null;
}

/**
 * 五个查询 + 归一化事件。`activeKind = null` 表示整卡未启用（非概览 tab），
 * 此时一个事件请求都不会发出（与改造前 RelatedEvents 未挂载时的请求数一致）。
 */
export function useStockEvents(symbol: string | undefined, activeKind: EventTab | null): StockEventsResult {
  const enabled = Boolean(symbol);

  const announcement = useQuery({
    queryKey: ["related-events", symbol, "announcements"],
    queryFn: () => fetchAnnouncements(symbol as string, 10),
    staleTime: STALE_TIME, enabled: enabled && activeKind === "announcements",
  });
  const dragon = useQuery({
    queryKey: ["related-events", symbol, "dragon-tiger"],
    // symbol 上送服务端（该股跨交易日）；客户端 filter 是**护栏**，不再是唯一过滤手段。
    // 有返回却筛不出本股 = 后端没按 symbol 过滤 → 抛错走错误态，绝不静默成「暂无上榜记录」。
    queryFn: async () => {
      const rows = await fetchDragonTiger(50, symbol);
      const mine = rows.filter((r) => r.symbol === symbol);
      if (rows.length > 0 && mine.length === 0) throw new UnfilteredResultError();
      return mine;
    },
    staleTime: STALE_TIME, enabled: enabled && activeKind === "dragon-tiger",
  });
  const block = useQuery({
    queryKey: ["related-events", symbol, "block-trades"],
    queryFn: () => fetchBlockTrades(symbol as string, 15),
    staleTime: STALE_TIME, enabled: enabled && activeKind === "block-trades",
  });
  const floats = useQuery({
    queryKey: ["related-events", symbol, "share-floats"],
    queryFn: () => fetchShareFloats(symbol as string, 15),
    staleTime: STALE_TIME, enabled: enabled && activeKind === "share-floats",
  });
  const repo = useQuery({
    queryKey: ["related-events", symbol, "repurchases"],
    queryFn: () => fetchRepurchases(symbol as string, 15),
    staleTime: STALE_TIME, enabled: enabled && activeKind === "repurchases",
  });

  /**
   * 只从**已取到数据**的查询派生事件：`data === undefined`（未加载/失败）不贡献标记。
   * 注意这不是「缺失当 0」——没有标记 ≠ 没有事件，只是这一类还没被加载；
   * 列表里的错误态/空态仍然各自如实显示。
   */
  const events = useMemo<StockEvent[]>(() => {
    const out: StockEvent[] = [];
    const push = (kind: StockEvent["kind"], raw: string | null | undefined) => {
      const date = isoDate(raw);
      if (date) out.push({ kind, date });
    };

    for (const row of announcement.data ?? []) push(TAB_TO_KIND.announcements, row.announceTime);
    for (const row of dragon.data ?? []) push(TAB_TO_KIND["dragon-tiger"], row.tradeDate);
    for (const row of block.data ?? []) push(TAB_TO_KIND["block-trades"], row.tradeDate);
    for (const row of floats.data ?? []) push(TAB_TO_KIND["share-floats"], row.floatDate);
    for (const row of repo.data ?? []) push(TAB_TO_KIND.repurchases, row.annDate);

    return out;
  }, [announcement.data, dragon.data, block.data, floats.data, repo.data]);

  return { announcement, dragon, block, floats, repo, events };
}
