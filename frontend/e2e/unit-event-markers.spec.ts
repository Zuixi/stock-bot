/**
 * `toEventMarkers` / `indexOfDate` 的纯函数单测（P1-2 brief 门禁）。
 *
 * 为什么放 `e2e/` 且用 Playwright 跑：仓库没有 vitest/jest，Playwright 自带
 * TypeScript 转译 + tsconfig `paths` 解析，`@/` 别名可直接用 —— 零新依赖
 * （与 `unit-kline-option.spec.ts` / `unit-stock-range-stats.spec.ts` 同款）。
 *
 * 为什么 import `lib/eventMarkers` 而不是页面/hook：那个文件没有任何运行时 import
 * （`@/app/theme` 只 import type，会被擦除），所以能在 Node 里直接求值；hook 文件
 * 会拉进 `shared/api/client`（模块求值期读 `import.meta.env`）直接炸。这条边界是刻意的。
 *
 * 六条用例对应 brief 的五条验收 + 顺序稳定性：
 * ①同日多类合并 `公告+1` ②超过 8 个只留最近 8 ③不在 bar 区间 → 不产生标记
 * ④空输入 ⑤顺序稳定（bar 顺序）⑥单条事件没有 `+N` 尾巴。
 */
import { test, expect } from "@playwright/test";
import {
  MAX_EVENT_BUBBLES,
  indexOfDate,
  toEventMarkers,
  type EventKind,
  type StockEvent,
} from "@/features/stock-detail/lib/eventMarkers";

/** 任意色值：本文件只验"取的是哪一类对应的槽位"，不验具体色号。 */
const PALETTE: Record<EventKind, string> = {
  announcement: "color-announcement",
  "dragon-tiger": "color-dragon-tiger",
  "block-trade": "color-block-trade",
  "share-float": "color-share-float",
  repurchase: "color-repurchase",
};

/** 六个交易日（升序，与图表 x 轴同序）。 */
const DATES = ["2026-10-09", "2026-10-10", "2026-10-13", "2026-10-14", "2026-10-15", "2026-10-16"];

const ev = (kind: EventKind, date: string, title = "t"): StockEvent => ({ kind, date, title });

test("① 同日多事件合并成一个气泡，标签带计数（公告+1）", () => {
  const markers = toEventMarkers(
    [ev("announcement", "2026-10-13"), ev("announcement", "2026-10-13")],
    DATES,
    PALETTE,
  );
  expect(markers).toEqual([{ date: "2026-10-13", label: "公告+1", color: PALETTE.announcement }]);

  // 同日不同类也合并：主导类型取优先级最高的「公告」，计数仍是**全部**事件数
  const mixed = toEventMarkers(
    [
      ev("repurchase", "2026-10-14"),
      ev("dragon-tiger", "2026-10-14"),
      ev("announcement", "2026-10-14"),
    ],
    DATES,
    PALETTE,
  );
  expect(mixed).toEqual([{ date: "2026-10-14", label: "公告+2", color: PALETTE.announcement }]);

  // 当日只有龙虎榜 → 主导类型是龙虎榜（颜色跟着类型走）
  const onlyDragon = toEventMarkers([ev("dragon-tiger", "2026-10-15")], DATES, PALETTE);
  expect(onlyDragon[0].color).toBe(PALETTE["dragon-tiger"]);
});

test("② 日期超过 8 个只保留最近 8 个（一根 K 线一个气泡）", () => {
  // 12 个交易日的 bar，事件落在每一天
  const dates = Array.from({ length: 12 }, (_, i) => `2026-09-${String(i + 1).padStart(2, "0")}`);
  const events = dates.map((date) => ev("announcement", date));

  const markers = toEventMarkers(events, dates, PALETTE);
  expect(markers).toHaveLength(MAX_EVENT_BUBBLES);
  expect(MAX_EVENT_BUBBLES).toBe(8);
  // 保留的是**最近** 8 个日期，最早的 4 个被裁掉
  expect(markers.map((m) => m.date)).toEqual(dates.slice(-8));

  // 上限可覆盖（测试与将来的"显示更多"都用得到）
  expect(toEventMarkers(events, dates, PALETTE, 3).map((m) => m.date)).toEqual(dates.slice(-3));
  expect(toEventMarkers(events, dates, PALETTE, 0)).toEqual([]);
});

test("③ 日期不在已加载的 bar 区间（上市前/停牌/聚合后不存在）不产生标记", () => {
  const markers = toEventMarkers(
    [
      ev("announcement", "2026-10-13"), // 命中
      ev("share-float", "2020-01-02"), // 上市前
      ev("block-trade", "2026-10-11"), // 周末：不是 bar 日期
    ],
    DATES,
    PALETTE,
  );
  expect(markers).toEqual([{ date: "2026-10-13", label: "公告", color: PALETTE.announcement }]);

  // 全部越界 → 空数组（不是"画到最近一根"）
  expect(
    toEventMarkers([ev("repurchase", "2019-05-05")], DATES, PALETTE),
  ).toEqual([]);
});

test("④ 空输入：没有事件 / 没有 bar，都返回空数组", () => {
  expect(toEventMarkers([], DATES, PALETTE)).toEqual([]);
  expect(toEventMarkers([ev("announcement", "2026-10-13")], [], PALETTE)).toEqual([]);
  expect(toEventMarkers([], [], PALETTE)).toEqual([]);
});

test("⑤ 输出顺序稳定：按 bar 顺序升序，与入参事件顺序无关", () => {
  const shuffled = [
    ev("announcement", "2026-10-15"),
    ev("dragon-tiger", "2026-10-09"),
    ev("block-trade", "2026-10-13"),
  ];
  const first = toEventMarkers(shuffled, DATES, PALETTE);
  expect(first.map((m) => m.date)).toEqual(["2026-10-09", "2026-10-13", "2026-10-15"]);
  // 同一输入重复调用结果逐字相等（无 Map 迭代顺序等隐性不确定性）
  expect(toEventMarkers(shuffled, DATES, PALETTE)).toEqual(first);
  // 换入参顺序不改变输出
  expect(toEventMarkers([...shuffled].reverse(), DATES, PALETTE)).toEqual(first);
});

test("⑥ 单条事件标签没有 +N 尾巴（+0 是无意义的噪声）", () => {
  const markers = toEventMarkers([ev("dragon-tiger", "2026-10-16", "日涨幅偏离值达7%")], DATES, PALETTE);
  expect(markers).toHaveLength(1);
  expect(markers[0].label).toBe("龙虎榜");
  expect(markers[0].label).not.toContain("+");
});

test("indexOfDate：命中返回下标，缺席返回 -1（点击定位的 no-op 判据）", () => {
  expect(indexOfDate(DATES, "2026-10-13")).toBe(2);
  expect(indexOfDate(DATES, "2026-10-16")).toBe(5);
  // 不在区间（周末/上市前）→ -1：调用方必须 no-op，不能"就近取一根"
  expect(indexOfDate(DATES, "2026-10-11")).toBe(-1);
  expect(indexOfDate([], "2026-10-13")).toBe(-1);
});
