import { expect, test, type Page } from "@playwright/test";

/**
 * 历史分位 / 板块连续性 chip E2E（Task 7）。
 *
 * 契约（plans/2026-10-12-...-trends.md Task 7 + 拍板 R6 / Ruling 21/22）：
 * - 分位 chip **必须带样本窗口**（`近 N 交易日分位`）——没有窗口的分位会被读成全历史；
 * - `insufficient_history=true` ⇒ 只显示「样本不足」，**绝不**给分位数字（不给假分位）；
 * - 板块连续性 chip 必须带 `attribution_note`（industry/region = 「按当前分类回溯，非当时成分」）
 *   与 `available_from`（「自 X 起累积」）——归属口径不能藏在代码里；
 * - **缺失 ≠ 0**：缺完整日（`missing_days`）的日子只按缺日披露，绝不当成 0 流入；
 *   末日缺值渲染 `--`，绝不 `0.00亿`。
 *
 * 全部走 `page.route` mock：本用例不依赖活栈数据（端点读时计算，缓存/真库状态不该决定测试结果）。
 */

// ── 短线情绪 Tab 的三张涨停卡（与本文件无关的端点也要满足，否则卡级降级会盖掉温度计）──

const LADDER = {
  as_of: "2026-10-09", as_of_prev: "2026-10-08", as_of_quality: "complete", source: "local_calc",
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
  ],
};

const SECTOR_LIMIT_UP = {
  as_of: "2026-10-09", as_of_quality: "complete", source: "local_calc", degraded_reason: null,
  unclassified_count: 0,
  items: [{ l3_code: "110703", l3_name: "生猪养殖", l1_code: "110000", l1_name: "农林牧渔",
            max_streak: 3, leader_symbol: "002714", leader_name: "牧原股份",
            leader_streak: 3, zt_count: 4 }],
};

const YESTERDAY = {
  as_of: "2026-10-09", as_of_prev: "2026-10-08", as_of_quality: "complete", source: "local_calc",
  degraded_reason: null, kpis: { n: 0, measured: 0 }, items: [],
};

const CALENDAR = [
  { trade_date: "2026-10-08", zt_count: 60, dt_count: 2, zb_count: 20, broken_rate: 0.25,
    yzt_avg_pct: 1.8, promo_1to2: 0.2, promo_2to3: 0.4, max_streak: 5 },
  { trade_date: "2026-10-09", zt_count: 75, dt_count: 1, zb_count: 39, broken_rate: 0.3421,
    yzt_avg_pct: 2.82, promo_1to2: 0.1585, promo_2to3: 0.3077, max_streak: 4 },
];

/**
 * `GET /market/sentiment/trends` 的**充足样本** payload（字段名照抄后端 snake_case）。
 * 三个指标的 `sample_count` 故意各不相同（239 / 231 / 228）：分位窗口是**该字段**的
 * 有效样本数，不是窗口行数 —— chip 上写窗口行数会谎报样本区间。
 */
const TRENDS_ENOUGH = {
  as_of: "2026-10-09",
  as_of_quality: "complete",
  window: { days: 250, from: "2025-10-15", to: "2026-10-09", samples: 239 },
  series: [
    { trade_date: "2026-10-08", zt_count: 60, dt_count: 2, zb_count: 20, broken_rate: 0.25,
      promo_1to2: 0.2, max_streak: 3 },
    { trade_date: "2026-10-09", zt_count: 75, dt_count: 1, zb_count: 39, broken_rate: 0.3421,
      promo_1to2: 0.1585, max_streak: 4 },
  ],
  percentile: {
    zt_count: { field: "zt_count", sample_count: 239, from: "2025-10-15", to: "2026-10-09",
                p0: 21, p10: 40, p25: 68, p50: 90, p75: 120, p90: 150, p100: 210 },
    broken_rate: { field: "broken_rate", sample_count: 231, from: "2025-10-15", to: "2026-10-09",
                   p0: 0.1, p10: 0.18, p25: 0.22, p50: 0.28, p75: 0.33, p90: 0.4, p100: 0.55 },
    promo_1to2: { field: "promo_1to2", sample_count: 228, from: "2025-10-15", to: "2026-10-09",
                  p0: 0.03, p10: 0.08, p25: 0.12, p50: 0.18, p75: 0.25, p90: 0.31, p100: 0.48 },
  },
  streak: { zt_above_median_days: 112 },
  insufficient_history: false,
};

/** 样本不足：`percentile=null`（后端 Ruling 4），前端只能显示「样本不足」。 */
const TRENDS_INSUFFICIENT = {
  ...TRENDS_ENOUGH,
  window: { days: 30, from: "2026-08-28", to: "2026-10-09", samples: 30 },
  percentile: null,
  insufficient_history: true,
};

async function routeSentimentTab(page: Page, trends: unknown) {
  await page.route("**/market/limit-up-ladder*", (r) => r.fulfill({ json: LADDER }));
  await page.route("**/market/sector-limit-up*", (r) => r.fulfill({ json: SECTOR_LIMIT_UP }));
  await page.route("**/market/yesterday-limit-up*", (r) => r.fulfill({ json: YESTERDAY }));
  await page.route("**/market/sentiment/calendar*", (r) => r.fulfill({ json: CALENDAR }));
  await page.route("**/market/sentiment/trends*", (r) => r.fulfill({ json: trends }));
}

// ── 资金流向 Tab：板块资金流信封 + 板块趋势 ────────────────────────────────

const MONEYFLOW = {
  as_of: "2026-10-09", stale_days: 0,
  items: [
    { board_code: "BK0475", board_name: "银行", pct_change: 1.2, main_net_inflow: 3.2e8,
      super_large_net: 1.1e8, large_net: 0.9e8, main_net_ratio: 5.1, up_count: 30, down_count: 5,
      lead_stock_name: "招商银行", lead_stock_code: "600036", lead_stock_pct: 2.3 },
    { board_code: "BK0420", board_name: "保险", pct_change: -0.4, main_net_inflow: -1.5e8,
      super_large_net: null, large_net: null, main_net_ratio: -2.2, up_count: 2, down_count: 6,
      lead_stock_name: null, lead_stock_code: null, lead_stock_pct: null },
  ],
};

/**
 * `GET /market/sector-trends` payload。
 *
 * - `保险` 的**末日 `main_net_inflow` 为 null**（该板最后完整日无行）→ chip 必须渲染 `--`；
 * - `missing_days` 含 2026-10-02：该日**不在**任何 `series` 里，只按缺日披露（缺日 ≠ 0 流入）。
 */
const SECTOR_TRENDS = {
  dimension: "industry",
  as_of: "2026-10-09",
  available_from: "2026-10-01",
  attribution_note: "按当前分类回溯，非当时成分",
  lu_count_source: "unavailable",
  top_n: 20,
  // `days`(请求值) 与 `observed_days`(实际观测跨度) 故意不等：表头必须两者都显示，
  // 否则"只取到 3 个交易日"会被读成"用户只请求了 3 天"（Task 8）。
  window: {
    days: 60,
    observed_days: 3,
    from: "2026-10-01",
    to: "2026-10-09",
    samples: 3,
  },
  boards: [
    { board_code: "BK0475", board_name: "银行", main_net_inflow: 3.2e8, pct_change: 1.2,
      up_count: 30, down_count: 5, streak_on_top_n: 3, max_streak_on_top_n: 4,
      streak_inflow: 2, max_streak_inflow: 2, streak_outflow: 0, max_streak_outflow: 0,
      series: [
        { trade_date: "2026-10-01", main_net_inflow: 1.1e8, pct_change: 0.5, up_count: 20,
          down_count: 10, lu_count: null },
        { trade_date: "2026-10-08", main_net_inflow: 2.4e8, pct_change: 0.9, up_count: 25,
          down_count: 8, lu_count: null },
        { trade_date: "2026-10-09", main_net_inflow: 3.2e8, pct_change: 1.2, up_count: 30,
          down_count: 5, lu_count: null },
      ] },
    { board_code: "BK0420", board_name: "保险", main_net_inflow: null, pct_change: null,
      up_count: null, down_count: null, streak_on_top_n: 0, max_streak_on_top_n: 1,
      streak_inflow: 0, max_streak_inflow: 1, streak_outflow: 0, max_streak_outflow: 0,
      series: [
        { trade_date: "2026-10-01", main_net_inflow: 0.5e8, pct_change: 0.2, up_count: 4,
          down_count: 3, lu_count: null },
      ] },
  ],
  missing_days: ["2026-10-02"],
  leader_change_count: 1,
  leader_changes: [{ trade_date: "2026-10-09", board_code: "BK0475", board_name: "银行" }],
};

/** 概念维度：时点成分 + 有 `lu_count`（`lu_count_source=point_in_time_membership`）。 */
const SECTOR_TRENDS_CONCEPT = {
  ...SECTOR_TRENDS,
  dimension: "concept",
  attribution_note: "成分按 concept_member_changes 反推的时点口径",
  lu_count_source: "point_in_time_membership",
  boards: [{ ...SECTOR_TRENDS.boards[0], series: [
    { trade_date: "2026-10-01", main_net_inflow: 1.1e8, pct_change: 0.5, up_count: 20,
      down_count: 10, lu_count: 2 },
    { trade_date: "2026-10-08", main_net_inflow: 2.4e8, pct_change: 0.9, up_count: 25,
      down_count: 8, lu_count: null },
    { trade_date: "2026-10-09", main_net_inflow: 3.2e8, pct_change: 1.2, up_count: 30,
      down_count: 5, lu_count: 3 },
  ] }],
};

async function routeMoneyflowTab(page: Page, conceptTrends: unknown = SECTOR_TRENDS_CONCEPT) {
  await page.route("**/market/sector-moneyflow*", (r) => r.fulfill({ json: MONEYFLOW }));
  await page.route("**/market/sector-trends*", (r) => {
    const dimension = new URL(r.request().url()).searchParams.get("dimension");
    return r.fulfill({ json: dimension === "concept" ? conceptTrends : SECTOR_TRENDS });
  });
}

/** 进 /market 并切到指定分类 Tab（antd Tabs：role=tab 可点）。 */
async function openTab(page: Page, name: string) {
  await page.goto("/market");
  await page.getByRole("tab", { name }).click();
}

// ── 分位 chip ────────────────────────────────────────────────────────────

test("情绪分位 chip 带该指标的样本窗口与分位梯", async ({ page }) => {
  await routeSentimentTab(page, TRENDS_ENOUGH);
  await openTab(page, "短线情绪");

  const block = page.locator('[data-testid="thermo-percentile"]');
  await expect(block).toBeVisible();
  // 样本不足分支不得出现
  await expect(page.locator('[data-testid="thermo-percentile-insufficient"]')).toHaveCount(0);

  // 计数指标：整数梯
  const zt = page.locator('[data-testid="thermo-pct-zt_count"]');
  await expect(zt).toContainText("近 239 交易日分位");
  await expect(zt).toContainText("P25 68");
  await expect(zt).toContainText("P50 90");
  await expect(zt).toContainText("P75 120");

  // 比值指标：×100 渲染（0.28 → 28.00%），且窗口取**该字段**的 sample_count（231 ≠ 239）
  const br = page.locator('[data-testid="thermo-pct-broken_rate"]');
  await expect(br).toContainText("近 231 交易日分位");
  await expect(br).toContainText("P50 28.00%");
  await expect(page.locator('[data-testid="thermo-pct-promo_1to2"]')).toContainText("P50 18.00%");

  // 样本区间（from → to）与连击统计必须上屏
  await expect(block).toContainText("2025-10-15");
  await expect(block).toContainText("2026-10-09");
  await expect(page.locator('[data-testid="thermo-streak"]')).toContainText("112");
});

test("insufficient_history：只显示「样本不足」，不给任何分位数字", async ({ page }) => {
  await routeSentimentTab(page, TRENDS_INSUFFICIENT);
  await openTab(page, "短线情绪");

  const marker = page.locator('[data-testid="thermo-percentile-insufficient"]');
  await expect(marker).toBeVisible();
  await expect(marker).toContainText("样本不足");
  // 窗口样本数如实（30 个交易日），但不得出现分位梯
  await expect(marker).toContainText("近 30 个交易日");
  await expect(marker).not.toContainText("%");
  // 整个分位区块都不许出现任何分位梯数字（负向断言锚在正向断言之后，否则「区块没渲染」也会绿）
  await expect(page.locator('[data-testid="thermo-percentile"]')).not.toContainText(/P\d\d/);
  // 三个分位 chip 一个都不许渲染
  await expect(page.locator('[data-testid="thermo-pct-zt_count"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="thermo-pct-broken_rate"]')).toHaveCount(0);
  await expect(page.locator('[data-testid="thermo-pct-promo_1to2"]')).toHaveCount(0);
});

test("趋势线复用既有 TrendSpark（分母来自 trends 序列时仍为 svg）", async ({ page }) => {
  await routeSentimentTab(page, TRENDS_ENOUGH);
  await openTab(page, "短线情绪");
  await expect(page.locator(".thermo-trend__svg")).toBeVisible();
  // 序列驱动：近 N 个交易日的区间文案在（不写死具体长度，避免把窗口大小测死）
  await expect(page.locator(".thermo-trend__range")).toContainText(/近 \d+ 个交易日/);
});

// ── 板块连续性 chip ───────────────────────────────────────────────────────

test("板块连续性 chip 带 attribution 标注与 available_from", async ({ page }) => {
  await routeMoneyflowTab(page);
  await openTab(page, "资金流向");

  const block = page.locator('[data-testid="sector-continuity"]');
  await expect(block).toBeVisible();
  // attribution_note 原样上屏（industry/region = 按当前分类回溯，非当时成分）
  await expect(page.locator('[data-testid="sector-continuity-attribution"]'))
    .toHaveText("按当前分类回溯，非当时成分");
  // available_from 必须显式（表内最早完整日，不是窗口起点）
  await expect(page.locator('[data-testid="sector-continuity-available-from"]'))
    .toContainText("自 2026-10-01 起累积");
  // 请求窗口与实际观测跨度必须并排显示（Task 8）：短观测不得读成短请求
  await expect(page.locator('[data-testid="sector-continuity-window"]'))
    .toContainText("请求近 60 交易日");
  await expect(page.locator('[data-testid="sector-continuity-window"]'))
    .toContainText("实际观察 3 个交易日");
  await expect(page.locator('[data-testid="sector-continuity-window"]'))
    .toContainText("2026-10-01 → 2026-10-09");

  // 连续性只来自完整日：3 天上榜 / 2 天连续净流入（后端在完整日上算，前端不重算）
  const bank = page.locator('[data-testid="sector-continuity-BK0475"]');
  await expect(bank).toContainText("银行");
  await expect(bank).toContainText("连续 3 天上榜");
  await expect(bank).toContainText("连续 2 天净流入");
  await expect(block).toContainText("龙头更替 1 次");
});

test("缺完整日的日子按缺日披露，末日缺值渲染 --（绝不 0 流入）", async ({ page }) => {
  await routeMoneyflowTab(page);
  await openTab(page, "资金流向");

  const missing = page.locator('[data-testid="sector-continuity-missing"]');
  await expect(missing).toBeVisible();
  await expect(missing).toContainText("缺完整日 1 天");
  await expect(missing).toContainText("2026-10-02");
  await expect(missing).toContainText("不计 0 流入");

  // 末日无行的板：-- 而不是 0.00亿（正向锚在文案上，避免「区块没渲染」也假绿）
  const insurance = page.locator('[data-testid="sector-continuity-BK0420"]');
  await expect(insurance).toContainText("保险");
  await expect(insurance).toContainText("末完整日 --");
  await expect(insurance).not.toContainText("0.00亿");
  await expect(page.locator('[data-testid="sector-continuity"]')).not.toContainText("2026-10-02：0");
});

test("切概念维度：连续性重新取数并换成时点成分口径（含 lu_count）", async ({ page }) => {
  await routeMoneyflowTab(page);
  await openTab(page, "资金流向");
  await expect(page.locator('[data-testid="sector-continuity-attribution"]'))
    .toHaveText("按当前分类回溯，非当时成分");

  await page.locator(".ant-card").filter({ hasText: "板块主力资金流" })
    .locator(".ant-segmented-item").filter({ hasText: "概念" }).click();
  await expect(page.locator('[data-testid="sector-continuity-attribution"]'))
    .toHaveText("成分按 concept_member_changes 反推的时点口径");
  // lu_count 有值才渲染（unavailable 时不得显示 0 家）
  await expect(page.locator('[data-testid="sector-continuity-BK0475"]')).toContainText("当日涨停 3 家");
});
