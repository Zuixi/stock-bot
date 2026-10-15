/**
 * 标的清单的**指标三元组契约**（P1-2）：最新价 · 涨跌额 · 涨跌幅。
 *
 * 为什么要单独一个模块：改造前「榜单 / 自选 / 龙虎榜 / 大宗 / 解禁 / 回购」各自决定
 * 显示哪几个数、各写一套列宽，于是同一批标的的数字**无法跨表竖读**——眼睛在一张表里
 * 记住「最新价」的位置，换一张表同一位置却是「成交额」。owner 批准的竞品（FinScope）
 * 之所以"密但可扫"，正是把三元组做成**固定列位 + 右对齐 + 等宽数字**：读多张表时
 * 不需要重新找列。本模块就是这三条契约的**全站唯一真相**。
 *
 * 契约三条（机械可查：`e2e/marketDataFace.spec.ts` 与 `e2e/public-homepage.spec.ts`
 * 直接 import 本模块的常量去量 DOM，故"实现漂移"会被当场抓住）：
 * 1. **顺序固定**：最新价 → 涨跌额 → 涨跌幅（`TRIPLET_ORDER`）；
 * 2. **列宽固定**：`TRIPLET_WIDTH` 同时是 `DataRow` 栅格列宽（`TRIPLET_GRID_TEMPLATE`）
 *    与 antd 表格 `column.width` 的来源。两处各写一套宽度的话，"跨表竖读"当场失效；
 * 3. **右对齐 + 等宽数字**：槽位一律 `text-align: right` 且带 `.num`（tabular figures）。
 *
 * ⚠️ **不编造载荷里没有的数字**：某个成员在某个端点里不存在时，槽位照样**占位**并渲染
 * `--`（仓库铁律：缺失绝不回落 0）。留空槽位是有意的——槽位塌陷会让它右边的成员
 * 左右跳，恰恰破坏"竖读"。哪些端点缺哪个成员，见 task-31-report 的"载荷比对"一节。
 */
export const TRIPLET_WIDTH = {
  /** 最新价（或该清单的头部数值，如成交额/换手率） */
  price: 72,
  /** 涨跌额（绝对变动，单位由各清单的口径决定，默认元） */
  change: 62,
  /** 涨跌幅（%） */
  pct: 66,
} as const;

export type TripletMember = keyof typeof TRIPLET_WIDTH;

/** 固定列序；同时是遍历/断言的唯一来源（不许在消费方各写一遍数组）。 */
export const TRIPLET_ORDER = ["price", "change", "pct"] as const satisfies readonly TripletMember[];

/** 三元组的表头文案（表格表面用它，DataRow 表面靠列位与颜色自证）。 */
export const TRIPLET_LABEL: Record<TripletMember, string> = {
  price: "最新价",
  change: "涨跌额",
  pct: "涨跌幅",
};

/**
 * 三元组三列宽之和（**表格口径**：表格的列与列之间没有额外 gap，列宽就是全部）。
 * 各清单表格算"最小表宽"时只许用这个数，不要用栅格口径的 `TRIPLET_GRID_TEMPLATE`。
 */
export const TRIPLET_COLUMNS_WIDTH =
  TRIPLET_WIDTH.price + TRIPLET_WIDTH.change + TRIPLET_WIDTH.pct;

/**
 * `DataRow` 的栅格模板：三个槽位**固定列宽**。由 `DataRow` 内联注入，
 * 与 `TRIPLET_WIDTH` 是同一份数字（单一真相，不是抄一遍）。
 */
export const TRIPLET_GRID_TEMPLATE = `${TRIPLET_WIDTH.price}px ${TRIPLET_WIDTH.change}px ${TRIPLET_WIDTH.pct}px`;

/** 清单里非三元组数字列的宽度（标识 / 数量 / 金额）——跨表同样要对齐。 */
export const INSTRUMENT_COL_WIDTH = {
  /** 标识格（名称 + 代码同一格，见 `InstrumentList.tsx`）：标的清单统一的"小标识"形状 */
  identity: 110,
  /** 数量、股数、笔数一类的辅助数字列 */
  extra: 92,
} as const;
