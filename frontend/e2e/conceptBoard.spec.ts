import { expect, test } from "@playwright/test";

/**
 * 概念板块详情页 E2E（契约 §2.3 / §3 触点 B）：
 * - 「成分截至 X（东财）」必须上屏 —— 历史口径声明是契约要求，不是装饰；
 * - 缺失 ≠ 0：无行情成分的涨跌幅/换手/市值渲染 `--`，不得渲染 0/0.00%；
 * - 板内梯队来自 `/concepts/{code}` 的 echelons（与连板梯队卡同形）；
 * - 连板列同样由 echelons 拍平：不在梯队的成分渲染 `--`（缺失 ≠ 0 板）。
 *
 * 说明：T16 brief 的同一段断言里还有 `getByText("未开板")`，此处**刻意不含**：
 * `/concepts/{code}` 的响应（§2.2 ConceptKpisOut）没有未开板字段，mock 载荷里也没有，
 * 页面无从渲染；「未开板」属于次新股情绪卡（T15，见本文件第四个用例断言）。
 * 在这里断言它只会得到一个假绿（或假红），不测任何真实行为。
 */
const DETAIL = {
  as_of: "2026-09-18", membership_as_of: "2026-09-18", source: "em_clist", degraded_reason: null,
  board: { board_code: "BK0501", board_name: "次新股", member_count: 162, unresolved_count: 0,
    priced_count: 162, up_count: 157, flat_count: 0, down_count: 5, avg_pct: 4.66,
    main_net_inflow: 1234567890.0, main_net_ratio: 2.1, lead_stock_name: "某股",
    lead_stock_code: "601091", lead_stock_pct: 20.0,
    leaders: [{ symbol: "601091", name: "C沈鼓", change_percent: 20.0 }] },
  kpis: { zt_count: 9, max_streak: 3, leader_symbol: "601091", leader_name: "C沈鼓" },
  echelons: [{ streak: 3, label: "3连板", stocks: [{ symbol: "601091", name: "C沈鼓", streak: 3,
    days_span: 3, boards_in_window: 3, missing_days: 0, amount: 1.0e8,
    seal_time: null, seal_fund: null, break_count: null }] }],
  unresolved_count: 0, stock_count: 162,
};
const STOCKS = [
  { symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks", category: "stock",
    list_date: "2026-09-17", asof: "2026-05-08T00:00:00Z", latest_price: 20.8,
    change_percent: 20.0, turnover_rate: 89.08, circ_mv: 179664849567.0, amount: 2181187.373,
    latest_quote_date: "2026-09-18" },
  // 缺失值样本：无行情 → 涨跌幅/换手渲染 `--`，不得渲染 0
  { symbol: "688837", name: "C信诺维", exchange: "Shanghai_Stocks", category: "stock",
    list_date: null, asof: "2026-05-08T00:00:00Z", latest_price: null, change_percent: null,
    turnover_rate: null, circ_mv: null, amount: null, latest_quote_date: null },
  // 排序样本：源顺序最后一个、涨跌幅最高且不在梯队 —— 默认降序若没生效，它不会排首位
  // （否则"首行是 C沈鼓"与源顺序同形，断言假绿）；也不在 echelons 里 → 连板列必须 `--`
  { symbol: "301234", name: "C复核", exchange: "Shenzen_Stocks", category: "stock",
    list_date: "2026-09-16", asof: "2026-05-08T00:00:00Z", latest_price: 33.5,
    change_percent: 33.5, turnover_rate: 41.2, circ_mv: 8.0e9, amount: 3.3e6,
    latest_quote_date: "2026-09-18" },
];

test("概念详情页展示成分与板内梯队，并宣布成分口径", async ({ page }) => {
  await page.route("**/api/v1/concepts/BK0501/stocks", (r) => r.fulfill({ json: STOCKS }));
  await page.route("**/api/v1/concepts/BK0501", (r) => r.fulfill({ json: DETAIL }));
  await page.goto("/market/concept/BK0501");
  await expect(page.getByTestId("concept-board")).toBeVisible();
  await expect(page.getByRole("heading", { name: "次新股" })).toBeVisible();
  await expect(page.getByText(/成分截至/)).toBeVisible();          // 历史口径声明必须在页面上
  // KPI 瓦片（同一响应的 items 口径，不新造指标）
  await expect(page.getByText("板内涨停")).toBeVisible();
  await expect(page.getByText("12.35 亿")).toBeVisible();          // 元 → 亿
  await expect(page.getByText("96.9%")).toBeVisible();             // 157 / (157+0+5)
  // 板内梯队（复用 <LimitUpLadder>）
  const ladder = page.locator(".sentiment-ladder");
  await expect(ladder).toContainText("3连板");
  await expect(ladder).toContainText("C沈鼓");
  // 成分表：命中票渲染真实值；无行情票一律 `--`，不得渲染 0
  await expect(page.locator("tr", { hasText: "C沈鼓" })).toContainText("+20.00%");
  // 连板列（§3 触点 B）：echelons 拍平；命中票 `3板`，不在梯队的成分 `--`（缺失 ≠ 0 板）。
  // 断言收在**最后一格**（extraColumns 追加在既有列之后）：行内其它列也有 `--`，整行断言会假绿
  const hitRow = page.locator("tr", { hasText: "C沈鼓" });
  await expect(hitRow.locator("td").last()).toHaveText("3板");
  const missingRow = page.locator("tr", { hasText: "C信诺维" });
  await expect(missingRow).toContainText("--");
  await expect(missingRow).not.toContainText("0.00%");
  // 排序接线：默认涨跌幅降序 → 源顺序末位的 +33.50% 排首位（行序与表头箭头同源，不是死箭头）
  await expect(page.locator(".ant-table-tbody tr.ant-table-row").first()).toContainText("C复核");
  // 不在梯队的成分连板列 `--`（缺失 ≠ 0 板）
  const offLadderRow = page.locator("tr", { hasText: "C复核" });
  await expect(offLadderRow.locator("td").last()).toHaveText("--");
});
