import { expect, test } from "@playwright/test";

/**
 * 短线情绪 Tab E2E。契约（docs/design/limit-up-sentiment.md §5）：
 * - 本地自算路径下 封板时间 渲染 `--`（缺失 ≠ 0）
 * - 降级原因必须显示对应文案，不得显示"暂无数据"
 * - 三块各自降级：abort 一个端点，邻区仍正常
 * 2026-09-15 重设计：温度计根类 `.sentiment-header` → `.thermo`；
 * 比值字段（炸板率/晋级率）必须 ×100 渲染（后端 0.3421 → UI 34.21%）；
 * 环比 chip 与趋势线来自 `/sentiment/calendar`（无前值时只藏对应元素）。
 */
const LADDER = {
  as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc",
  limits_present: true, is_partial: false, sw_coverage: 0.9333, degraded_reason: null,
  kpis: { zt_count: 75, dt_count: 1, zb_count: 39, broken_rate: 0.3421, yzt_avg_pct: 2.82,
          yzt_avg_open_premium: 1.1, yzt_n: 95, promo_1to2: 0.1585, promo_1to2_n: 82,
          promo_1to2_noisy: false, promo_2to3: 0.3077, promo_2to3_n: 13,
          promo_2to3_noisy: false, max_streak: 4 },
  echelons: [
    { streak: 4, label: "4连板", stocks: [
      { symbol: "600354", name: "敦煌种业", streak: 4, days_span: 4, boards_in_window: 4,
        missing_days: 0, sw_l1_name: "农林牧渔", sw_l3_name: "种植业",
        seal_time: null, seal_fund: null, break_count: null } ] },
    { streak: 1, label: "首板", stocks: [
      { symbol: "002274", name: "华昌化工", streak: 1, days_span: 5, boards_in_window: 5,
        missing_days: 5, sw_l1_name: "基础化工", sw_l3_name: "化学原料",
        seal_time: null, seal_fund: null, break_count: null } ] },
  ],
};

/** 两点历史：as_of=09-08 的前一交易日为 09-05，环比 chip 与趋势线都应出现。 */
const CALENDAR = [
  { trade_date: "2026-09-05", zt_count: 60, dt_count: 3, zb_count: 30, broken_rate: 0.3333,
    yzt_avg_pct: 1.8, promo_1to2: 0.2, promo_2to3: 0.4, max_streak: 5 },
  { trade_date: "2026-09-08", zt_count: 70, dt_count: 2, zb_count: 38, broken_rate: 0.35,
    yzt_avg_pct: 2.5, promo_1to2: 0.16, promo_2to3: 0.3, max_streak: 4 },
];

function routeCalendar(page: import("@playwright/test").Page, json: unknown) {
  return page.route("**/market/sentiment/calendar*", (r) => r.fulfill({ json }));
}

test("情绪 Tab 展示梯队并遵守缺失值契约", async ({ page }) => {
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", source: "local_calc", degraded_reason: null,
    unclassified_count: 2,
    items: [{ l3_code: "110703", l3_name: "生猪养殖", l1_code: "110000", l1_name: "农林牧渔",
              max_streak: 3, leader_symbol: "002714", leader_name: "牧原股份",
              leader_streak: 3, zt_count: 4 }] } }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc", degraded_reason: null,
    kpis: { n: 2, measured: 1, yzt_avg_pct: 10.0, yzt_avg_open_premium: 5.0 },
    items: [
      { symbol: "000001", name: "平安银行", prev_streak: 1, today_pct: 10.0,
        today_open_premium: 5.0, today_streak: 2, is_lu: true, touched: true, broken: false,
        suspended: false, missing_days: 0, sw_l3_name: "银行" },
      { symbol: "000002", name: "万科A", prev_streak: 1, today_pct: null,
        today_open_premium: null, today_streak: null, is_lu: false, touched: false,
        broken: false, suspended: true, missing_days: 1, sw_l3_name: "房地产" },
    ] } }));
  await routeCalendar(page, CALENDAR);

  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();

  // 梯队
  await expect(page.locator(".sentiment-ladder")).toContainText("4连板");
  await expect(page.locator(".sentiment-ladder")).toContainText("敦煌种业");
  // N天M板：boards_in_window(5) ≠ streak(1) → 必须渲染「5天5板」（窗口被截成两天时会变成 2 天以下）
  await expect(page.locator(".sentiment-ladder")).toContainText("5天5板");
  // 本地路径无封板时间 → `--`
  await expect(page.locator(".sentiment-ladder")).toContainText("--");
  // 停牌披露
  await expect(page.locator(".sentiment-ladder")).toContainText("缺少");
  // 温度计
  await expect(page.locator(".thermo")).toContainText("75");
  await expect(page.locator(".thermo")).toContainText("39");
  // 比值字段必须 ×100：broken_rate 0.3421 → 34.21%（历史 bug 是显示 0.34%）
  await expect(page.locator(".thermo")).toContainText("34.21%");
  // 晋级率同理：0.1585 → 15.85%
  await expect(page.locator(".thermo")).toContainText("15.85%");
  // 环比 chip：涨停 75 vs 前日 70 → +5；有前值时趋势线（svg）出现
  await expect(page.locator(".thermo").getByText("+5", { exact: true })).toBeVisible();
  await expect(page.locator(".thermo-trend__svg")).toBeVisible();
  // 申万 L3 热度榜：L1 过滤 tags 渲染、行列表含龙头；「仅看 ≥2 板」开关存在
  await expect(page.locator(".sector-limit-up")).toContainText("生猪养殖");
  await expect(page.locator(".sector-limit-up")).toContainText("牧原股份");
  await expect(page.locator(".sector-limit-up")).toContainText("仅看 ≥2 板");
  // 昨日涨停卡：卡头是表现日，卡内必须显式标注涨停日样本口径（防「数据落后」误读）
  await expect(page.getByText(/统计 9月7日 涨停股在 9月8日 的表现/)).toBeVisible();
  // 昨日表现：停牌票不得渲染 0.00%
  const suspendedRow = page.locator("tr", { hasText: "万科A" });
  await expect(suspendedRow).toContainText("停牌");
  await expect(suspendedRow).not.toContainText("0.00%");
});

test("限价缺失时显示原因而非空态", async ({ page }) => {
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: {
    ...LADDER, limits_present: false, degraded_reason: "price_limits_missing",
    kpis: null, echelons: [] } }));
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: {
    as_of: null, source: "local_calc", degraded_reason: "price_limits_missing",
    unclassified_count: 0, items: [] } }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: null, as_of_prev: null, source: "local_calc",
    degraded_reason: "price_limits_missing", kpis: {}, items: [] } }));
  await routeCalendar(page, []);

  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.getByText(/涨跌停价尚未回补/)).toBeVisible();
  await expect(page.getByText("暂无数据")).toHaveCount(0);
});

test("单端点失败不牵连邻区", async ({ page }) => {
  await page.route("**/market/sector-limit-up*", (r) => r.abort());
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: {
    as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc",
    degraded_reason: null, kpis: { n: 0, measured: 0 }, items: [] } }));
  await routeCalendar(page, CALENDAR);
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.locator(".sentiment-ladder")).toContainText("敦煌种业");
  await expect(page.locator(".sector-limit-up")).toBeVisible();
});

// ───────────────────────────────────────────────────────────────────────────
// Task 13：盘中 / 收盘双口径切换 + 分时曲线。
// 契约：切换控件默认「收盘」（首屏行为不变）；切「盘中」后三张涨停卡带 mode=intraday
// 重取，卡头透传后端 as_of_label（"盘中 HH:MM" 或回落文案 "盘中不可用，已回落收盘"），
// 并在梯队处渲染盘中分时曲线（0/1 点退化为空态，不崩）。
// antd 5 Segmented 的 radio input 是零尺寸隐藏元素（Playwright 判 hidden），断言与点击
// 一律落在可见的 `.ant-segmented-item` 上、选中态以 `selected` 类为准（同 kline.spec.ts）。
// ───────────────────────────────────────────────────────────────────────────

/** 盘中口径的梯队友情 payload：as_of_label 必须原样透传，不得二次美化。 */
const INTRADAY_LADDER = {
  ...LADDER,
  source: "eastmoney_intraday",
  as_of_quality: "partial",
  as_of_prev: null,
  sw_coverage: null,
  as_of_label: "盘中 10:35",
};

const SECTOR_CLOSE = {
  as_of: "2026-09-08", source: "local_calc", degraded_reason: null,
  unclassified_count: 2,
  items: [{ l3_code: "110703", l3_name: "生猪养殖", l1_code: "110000", l1_name: "农林牧渔",
            max_streak: 3, leader_symbol: "002714", leader_name: "牧原股份",
            leader_streak: 3, zt_count: 4 }],
};

const YESTERDAY_CLOSE = {
  as_of: "2026-09-08", as_of_prev: "2026-09-07", source: "local_calc", degraded_reason: null,
  kpis: { n: 0, measured: 0 }, items: [],
};

/** 两个升序分时点（>=2 点才画线）。 */
const INTRADAY_POINTS = [
  { id: 1, trade_date: "2026-09-08", captured_at: "2026-09-08T02:35:00+00:00",
    zt_count: 42, dt_count: 1, zb_count: 8, max_streak: 3 },
  { id: 2, trade_date: "2026-09-08", captured_at: "2026-09-08T02:40:00+00:00",
    zt_count: 55, dt_count: 0, zb_count: 11, max_streak: 4 },
];

/** 情绪 Tab 三个涨停端点按 URL 的 `mode` 分流（缺省=收盘），外加分时端点。 */
async function routeSentiment(
  page: import("@playwright/test").Page,
  { ladderIntraday = INTRADAY_LADDER, points = INTRADAY_POINTS } = {},
) {
  await page.route("**/market/limit-up-ladder*", (r) => {
    const mode = new URL(r.request().url()).searchParams.get("mode");
    return r.fulfill({ json: mode === "intraday" ? ladderIntraday : LADDER });
  });
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: SECTOR_CLOSE }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: YESTERDAY_CLOSE }));
  await page.route("**/market/sentiment/intraday*", (r) => r.fulfill({ json: points }));
  await routeCalendar(page, CALENDAR);
}

test("盘中/收盘切换默认收盘，切盘中后显示盘中口径与分时曲线", async ({ page }) => {
  await routeSentiment(page);
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();

  const modeSwitch = page.locator('[data-testid="sentiment-mode-switch"]');
  const segItem = (text: string) => modeSwitch.locator(".ant-segmented-item").filter({ hasText: text });

  // (a) 切换控件存在
  await expect(segItem("盘中")).toBeVisible();
  await expect(segItem("收盘")).toBeVisible();
  // (d) 默认 = 收盘（首屏行为不变），且此时不渲染盘中口径标签
  await expect(segItem("收盘")).toHaveClass(/ant-segmented-item-selected/);
  await expect(segItem("盘中")).not.toHaveClass(/ant-segmented-item-selected/);
  await expect(page.locator(".section-card__asof-label")).toHaveCount(0);

  // 切到盘中
  await segItem("盘中").click();
  // (b) 卡头透传 as_of_label（"盘中 HH:MM"）
  await expect(page.locator(".section-card__asof-label")).toContainText(/盘中 \d\d:\d\d/);
  // (c) 分时曲线 svg 出现
  await expect(page.locator('[data-testid="sentiment-intraday-chart"] svg')).toBeVisible();
});

test("盘中回落收盘时卡头如实显示回落文案", async ({ page }) => {
  await routeSentiment(page, {
    ladderIntraday: { ...INTRADAY_LADDER, as_of_label: "盘中不可用，已回落收盘" },
  });
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await page
    .locator('[data-testid="sentiment-mode-switch"]')
    .locator(".ant-segmented-item")
    .filter({ hasText: "盘中" })
    .click();
  await expect(page.locator(".section-card__asof-label")).toHaveText("盘中不可用，已回落收盘");
});

test("分时序列为空时渲染空态而非崩溃", async ({ page }) => {
  await routeSentiment(page, { points: [] });
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await page
    .locator('[data-testid="sentiment-mode-switch"]')
    .locator(".ant-segmented-item")
    .filter({ hasText: "盘中" })
    .click();
  await expect(page.locator('[data-testid="sentiment-intraday-empty"]')).toBeVisible();
  await expect(page.locator('[data-testid="sentiment-intraday-chart"] svg')).toHaveCount(0);
});

test("分时序列涨停家数恒定时不产生 NaN 路径", async ({ page }) => {
  // max === min：朴素归一化会 0/0 → NaN，整条路径失效。此用例守住 y 域退化分支。
  await routeSentiment(page, {
    points: [
      { id: 1, trade_date: "2026-09-08", captured_at: "2026-09-08T02:35:00+00:00",
        zt_count: 50, dt_count: 1, zb_count: 8, max_streak: 3 },
      { id: 2, trade_date: "2026-09-08", captured_at: "2026-09-08T02:40:00+00:00",
        zt_count: 50, dt_count: 1, zb_count: 8, max_streak: 3 },
    ],
  });
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await page
    .locator('[data-testid="sentiment-mode-switch"]')
    .locator(".ant-segmented-item")
    .filter({ hasText: "盘中" })
    .click();
  const chart = page.locator('[data-testid="sentiment-intraday-chart"]');
  await expect(chart.locator("svg")).toBeVisible();
  await expect(chart.locator("path.intraday__line")).not.toHaveAttribute("d", /NaN/);
});
