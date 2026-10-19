import type { ThemePalette } from "@/app/theme";

/**
 * 事件 → K 线气泡标记的**纯函数层**（P1-2 / D1「图内标记 + 可点列表 + 定位联动」）。
 *
 * 为什么单独成文件：`shared/ui/kline` 必须保持**领域无关**（它不认识公告/龙虎榜），
 * 所以「把五类事件压成一维日期序列上的气泡」这件事只能发生在 stock-detail 域；
 * 域内再把它拆成"纯取值 + hook 编排 + 组件"三层，纯函数才能脱离 React/ECharts 单测
 * （`e2e/unit-event-markers.spec.ts` 用 Playwright 直跑 TS，零新依赖）。
 *
 * 三条硬规则（brief 验收）：
 * 1. **一根 K 线最多一个气泡**：同日多类事件合并成一条，标签带计数（`公告+1`）。
 *    不合并的话，同一根蜡烛上会叠 N 个 markPoint，视觉上是一坨。
 * 2. **只标最近 `maxBubbles`(8) 个日期**：多到几十个气泡时图会被标记淹没，
 *    而"最近发生了什么"才是详情页要回答的问题（历史事件仍可在下方列表里翻）。
 * 3. **日期不在已加载的 bar 区间 → 不产生标记**（上市前 / 停牌 / 周月聚合后不存在的日子）。
 *    这是「缺失 ≠ 0」在图上的版本：在图上硬画一个不在 x 轴上的点，只会落到错误位置。
 *
 * 本文件**不含任何色值**：颜色由调用方按当前主题传入（`eventMarkerPalette` 只做
 * kind → 主题色槽的映射，不写死 hex），这样纯函数可测、暗色可区分。
 */

export type EventKind = "announcement" | "dragon-tiger" | "block-trade" | "share-float" | "repurchase";

/**
 * 一条归一化后的事件（域内唯一形状；各 API 的字段差异在 hook 里消化）。
 *
 * **只有图上标记真正消费的字段**：标题/数值/外链已经在 `RelatedEvents` 的表格列里
 * 渲染（那才是它们的唯一真源）。这里再抄一份 title/meta 只会变成"看着有用、
 * 其实没人读"的死构造，下一个读者还会以为改它能让列表变样。
 */
export interface StockEvent {
  kind: EventKind;
  /** 事件日，`YYYY-MM-DD`（与 K 线 bar 的 `date` 同形，可直接 + 严格比较） */
  date: string;
}

/** K 线气泡标记：`date` 必须能在 bar 日期里**精确**命中，否则不画。 */
export interface EventMarker {
  date: string;
  label: string;
  color: string;
}

export const EVENT_KIND_LABELS: Record<EventKind, string> = {
  announcement: "公告",
  "dragon-tiger": "龙虎榜",
  "block-trade": "大宗",
  "share-float": "解禁",
  repurchase: "回购",
};

/**
 * 同日多类事件时「主导类型」的优先级（越靠前越优先）。
 * 口径：公告（最可能是当日异动的解释）> 龙虎榜 > 大宗 > 解禁 > 回购。
 * 气泡只有一种颜色，必须挑一个类型上色；计数仍然反映**全部**事件数。
 */
export const EVENT_KIND_PRIORITY: readonly EventKind[] = [
  "announcement",
  "dragon-tiger",
  "block-trade",
  "share-float",
  "repurchase",
];

/** 图上事件气泡上限（brief：最多 8 个，一根 K 线一个）。 */
export const MAX_EVENT_BUBBLES = 8;

/** 主题色板里可用作标记色的槽位（`mag` 是灰阶数组，不适合当类型色，排除）。 */
type PaletteSlot = Exclude<keyof ThemePalette, "mag">;

/**
 * kind → 主题色**槽位**（不是色值）。调用方传当前主题的 `ThemePalette` 进来取色，
 * 于是同一份映射在明暗两套色板下各取各的值 —— 不新增 hex，也自动跟随主题。
 *
 * 明暗两套的可辨识度（暗底 `#1e222d`）：蓝 `primary` / 橙 `regime-hot` /
 * 紫 `regime-cooling` / 琥珀 `sent-extreme-hot` / 钢蓝 `regime-cold` 五个色相不同、
 * 明度也错开（浅色下橙与琥珀最接近，仍有明度差：`#A25910` vs `#a35f00`）。
 */
export const EVENT_MARKER_COLOR_SLOTS: Record<EventKind, PaletteSlot> = {
  announcement: "primary",
  "dragon-tiger": "regime-hot",
  "block-trade": "regime-cooling",
  "share-float": "sent-extreme-hot",
  repurchase: "regime-cold",
};

/** 当前主题下 kind → 色值。纯函数：只读入参，不碰 DOM / CSS var。 */
export function eventMarkerPalette(colors: ThemePalette): Record<EventKind, string> {
  const out = {} as Record<EventKind, string>;
  for (const kind of EVENT_KIND_PRIORITY) out[kind] = colors[EVENT_MARKER_COLOR_SLOTS[kind]];
  return out;
}

/**
 * 事件日期在 bar 日期里的下标；不在（上市前 / 停牌 / 周期聚合后不存在）返回 `-1`。
 *
 * 严格相等而不是 `<=` 取最近：把「10-13 的公告」画到「10-12 的蜡烛」上，
 * 会让用户以为事件发生在另一天 —— 宁可不动（调用方 no-op），也不撒谎。
 */
export function indexOfDate(dates: string[], date: string): number {
  return dates.indexOf(date);
}

/**
 * 把事件列表压成 K 线气泡：按日期聚合 → 丢掉不在 bar 区间内的 → 只留最近 `maxBubbles` 个 → 上色。
 *
 * - `dates` 是**已加载的 bar 日期**（升序，与图表 x 轴一致）；返回顺序即 bar 顺序（升序）。
 * - 同日多条：计数 `n`，标签 `主导类型标签 + (n-1)`（`n === 1` 时没有 `+0` 尾巴）。
 * - `palette` 由调用方给（`eventMarkerPalette(colors)`），本函数不认识主题。
 */
export function toEventMarkers(
  events: StockEvent[],
  dates: string[],
  palette: Record<EventKind, string>,
  maxBubbles: number = MAX_EVENT_BUBBLES,
): EventMarker[] {
  const limit = Number.isFinite(maxBubbles) ? Math.max(0, Math.floor(maxBubbles)) : 0;
  if (limit === 0 || events.length === 0 || dates.length === 0) return [];

  // bar 顺序（而不是字符串排序）：周/月聚合或非补零日期串时，x 轴顺序才是唯一真源
  const orderOf = new Map<string, number>();
  for (const [i, date] of dates.entries()) {
    if (!orderOf.has(date)) orderOf.set(date, i);
  }

  const byDate = new Map<string, StockEvent[]>();
  for (const event of events) {
    // 不在已加载区间（上市前/停牌/聚合后不存在的日子）→ 不产生标记
    if (!orderOf.has(event.date)) continue;
    const group = byDate.get(event.date);
    if (group) group.push(event);
    else byDate.set(event.date, [event]);
  }
  if (byDate.size === 0) return [];

  const keptDates = [...byDate.keys()]
    .sort((a, b) => (orderOf.get(a) ?? 0) - (orderOf.get(b) ?? 0))
    .slice(-limit); // 最近 N 个**日期**（不是最近 N 条事件）

  return keptDates.map((date) => {
    const group = byDate.get(date) as StockEvent[];
    const dominant = EVENT_KIND_PRIORITY.find((kind) => group.some((e) => e.kind === kind)) ?? group[0].kind;
    const base = EVENT_KIND_LABELS[dominant];
    return {
      date,
      label: group.length > 1 ? `${base}+${group.length - 1}` : base,
      color: palette[dominant],
    };
  });
}
