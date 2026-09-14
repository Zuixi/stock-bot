import { expect, test } from "@playwright/test";

/**
 * 短线情绪 Tab E2E。契约（docs/design/limit-up-sentiment.md §5）：
 * - 本地自算路径下 封板时间 渲染 `--`（缺失 ≠ 0）
 * - 降级原因必须显示对应文案，不得显示"暂无数据"
 * - 三块各自降级：abort 一个端点，邻区仍正常
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
  await expect(page.locator(".sentiment-header")).toContainText("75");
  await expect(page.locator(".sentiment-header")).toContainText("39");
  // 昨日表现：停牌票不得渲染 0.00%
  const suspendedRow = page.locator("tr", { hasText: "万科A" });
  await expect(suspendedRow).toContainText("停牌");
  await expect(suspendedRow).not.toContainText("0.00%");
  // 申万 L3
  await expect(page.locator(".sector-limit-up")).toContainText("生猪养殖");
  await expect(page.locator(".sector-limit-up")).toContainText("牧原股份");
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
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  await expect(page.locator(".sentiment-ladder")).toContainText("敦煌种业");
  await expect(page.locator(".sector-limit-up")).toBeVisible();
});
