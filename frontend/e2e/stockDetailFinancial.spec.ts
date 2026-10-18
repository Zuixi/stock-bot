import { test, expect as baseExpect, type Locator, type Page } from "@playwright/test";

/**
 * 概览页「成长与盈利」卡接真实数据（P0-1 E2E）。
 *
 * 背景：改造前该卡读 `stock.roe` / `stock.revenueGrowth` / `stock.profitGrowth`，
 * 而这三个字段**后端响应里根本不存在**（`StockOut`/`StockEnrichedOut` 无对应列），
 * 于是三张卡永远显示 `--`。真实数据在
 * `GET /api/v1/exchanges/{exchange}/stocks/{symbol}/financial-summary` 的
 * `metrics["roe"|"revenue_yoy"|"profit_yoy"].value`（单位 %）。
 *
 * 三条用例分工（缺一条就会有一类回归静默通过）：
 * 1. **有值**：ROE/营收同比/净利润同比都是数字（**不允许** `--`），ROE 格有质量徽标，
 *    卡片级报告期存在；且切财务 tab 后 `/financial-summary` 请求数仍为 1（缓存命中）；
 * 2. **null**：三格 `--`、不是 `0.00%`、无徽标、无报告期（缺失 ≠ 0）；
 * 3. **`value: 0`**：必须显示 `0.00%` 而不是 `--`（防 `value ? x : "--"` 这类 falsy 判断）。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

/** 只统计详情页的财务摘要请求；`financial-statements` / `financial-metrics/history` 不算。 */
const SUMMARY_PATH = "/financial-summary";

function recordSummaryRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes(SUMMARY_PATH)) urls.push(request.url());
  });
  return urls;
}

/** 卡片内某一指标格（antd Col）：标题文本（ROE / 营收同比 / 净利润同比）唯一可区分。 */
function metricCell(card: Locator, label: string): Locator {
  return card.locator(".ant-col").filter({ hasText: label }).first();
}

/** 指标格的数值区（Statistic 的 content），`--` 与 `17.95%` / `+1.47%` 都在这里。 */
function metricValue(cell: Locator): Locator {
  return cell.locator(".ant-statistic-content").first();
}

const growthCard = (page: Page) => page.locator(".ant-card").filter({ hasText: "成长与盈利" });

/**
 * 造一份 `financial-summary` 响应并挂上 route。
 * `metrics` 逐字传入：`undefined` = 该键整键缺席（后端可以整键不返回），
 * `{ value: null }` = 键在但值缺失，`{ value: 0 }` = 合法的零值。
 */
async function stubSummary(
  page: Page,
  metrics: Record<string, unknown>,
  latestPeriod: string | null = null,
) {
  await page.route("**/financial-summary*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        exchange: "Shanghai_Stocks",
        symbol: "600519",
        name: "贵州茅台",
        latest_period: latestPeriod,
        report_type: latestPeriod ? "H1" : null,
        ann_date: null,
        as_of: null,
        source: "tushare",
        metrics,
      }),
    });
  });
}

test("概览「成长与盈利」卡：三项均为真实数字 + 徽标 + 报告期，切财务 tab 不重复请求", async ({
  page,
}) => {
  // ⚠️ 监听必须在 goto 之前：首个请求在页面挂载时立刻发出，晚注册就漏记
  const summaryRequests = recordSummaryRequests(page);

  await page.goto("/stock/600519");

  const card = growthCard(page);
  await expect(card).toBeVisible();

  // ---- ROE：必须是数字（回归点：改造前这里永远是 `--`） ----
  const roeCell = metricCell(card, "ROE");
  await expect(roeCell).toBeVisible();
  const roeValue = metricValue(roeCell);
  // 数字形态：`17.95%`（不带正负号 —— ROE 是存量比率，不是"变化"，见 FundamentalCards 注释）
  await expect(roeValue).toHaveText(/\d/);
  await expect(roeValue).not.toHaveText(/^--$/);
  await expect(roeValue).toHaveText(/^-?\d+(\.\d+)?%$/);

  // 有值 ⇒ ROE 格必须有质量徽标（「财报」/「估算」；brief 要求 quality 徽标）
  await expect(roeCell, "ROE 有值时必须显示质量徽标").toContainText(/财报|估算/);

  // ---- 营收同比 / 净利润同比：同样**硬断言**是数字 ----
  // 600519 当前三项齐全（实测 `+1.47%` / `-1.95%`），因此没有任何理由容忍 `--`：
  // 容忍会让"三张卡又变回 --"这个本任务修的 bug 原样滑过门禁。
  for (const label of ["营收同比", "净利润同比"]) {
    const cell = metricCell(card, label);
    await expect(cell, `${label} 格应存在`).toBeVisible();
    const value = metricValue(cell);
    // 同比是变化量：走 ChangeText，带符号（`+1.47%` / `-1.95%`）
    await expect(value, `${label} 必须显示数字（不允许 --）`).toHaveText(/^[+-]?\d+(\.\d+)?%$/);
    await expect(cell, `${label} 有值时必须显示质量徽标`).toContainText(/财报|估算/);
  }

  // ---- 报告期：**卡片级**显示一次（brief：卡内加 latest_period，不是每格重复） ----
  await expect(card, "卡片应显示后端 latest_period").toContainText(/报告期 \d{4}-\d{2}-\d{2}/);
  await expect(card.getByText(/报告期/), "报告期只应出现一次").toHaveCount(1);

  // ---- 请求数：概览加载完成后 `/financial-summary` 恰好 1 次 ----
  // 先让在途请求落地，再统计（避免"断言时还没发出来"造成假绿）
  await page.waitForLoadState("networkidle").catch(() => {});
  expect(
    summaryRequests,
    `概览页应只请求 1 次 financial-summary，实际 ${summaryRequests.length} 次：\n${summaryRequests.join("\n")}`,
  ).toHaveLength(1);

  // ---- 切「财务」tab：同 query key ⇒ 命中缓存，请求数**仍是 1** ----
  await page.getByRole("tab", { name: "财务", exact: true }).click();
  // 锚点：财务 tab 自己的卡片可见，证明 tab 已真正挂载（而不是只切了个高亮）
  await expect(page.locator(".ant-card").filter({ hasText: "财务摘要" })).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(
    summaryRequests,
    `切到财务 tab 后不应再请求 financial-summary（应为同一 query key 命中缓存），` +
      `实际 ${summaryRequests.length} 次：\n${summaryRequests.join("\n")}`,
  ).toHaveLength(1);
});

test("财务摘要缺失时三张卡显示 --（不是 0），且不挂报告期与质量徽标", async ({ page }) => {
  // 「缺失 ≠ 0」是本卡最容易被写坏的口径（默认 0 会把"没有数据"说成"零增长"）。
  // 这里造：ROE 键在但 value=null、另两键**整键缺席**、latest_period=null。
  await stubSummary(page, { roe: { value: null, unit: "%", quality: null, source: null } });

  await page.goto("/stock/600519");

  const card = growthCard(page);
  await expect(card).toBeVisible();

  for (const label of ["ROE", "营收同比", "净利润同比"]) {
    const cell = metricCell(card, label);
    const value = metricValue(cell);
    await expect(value, `${label} 缺失时应渲染 --`).toHaveText("--");
    // 仍保留：这一条守的是"null 被写成 0"（`value ?? 0`），与上面守的"null → --"是两个 bug
    await expect(value, `${label} 缺失不得被写成 0`).not.toHaveText(/^0(\.0+)?%?$/);
    await expect(cell, `${label} 缺失时不应有质量徽标`).not.toContainText(/财报|估算/);
  }

  // 报告期是卡片级的：一格有值都没有 ⇒ 整卡不出现报告期
  await expect(card, "无任何数值时不应显示报告期").not.toContainText(/\d{4}-\d{2}-\d{2}/);
  await expect(card.getByText(/报告期/)).toHaveCount(0);
});

test("ROE 为 0 时显示 0.00%（合法的零值，不是 --）", async ({ page }) => {
  // 防 `value ? x : "--"` 这类 falsy 判断：0 是**合法值**（真·零收益），
  // 它和"没有数据"是两件事。上一轮的实现用 `value == null` 判定，本用例钉住它不回归。
  await stubSummary(
    page,
    {
      roe: { value: 0, unit: "%", quality: "reported", source: "tushare" },
      revenue_yoy: { value: 0, unit: "%", quality: "reported", source: "tushare" },
      profit_yoy: { value: -0, unit: "%", quality: "reported", source: "tushare" },
    },
    "2026-06-30",
  );

  await page.goto("/stock/600519");

  const card = growthCard(page);
  await expect(card).toBeVisible();

  const roeValue = metricValue(metricCell(card, "ROE"));
  await expect(roeValue, "ROE=0 必须渲染 0.00%，不能是 --").toHaveText("0.00%");
  await expect(roeValue).not.toHaveText(/^--$/);

  // 0 也是"有值"：徽标与报告期照样要出现
  await expect(metricCell(card, "ROE"), "ROE=0 时徽标应存在").toContainText(/财报/);
  await expect(card, "ROE=0 时报告期应存在").toContainText(/报告期 2026-06-30/);

  // 同比两个 0 值走 ChangeText：0 渲染为 `0.00%`（无 `+`），同样不能是 `--`
  for (const label of ["营收同比", "净利润同比"]) {
    const value = metricValue(metricCell(card, label));
    await expect(value, `${label}=0 应渲染 0.00%`).toHaveText(/^[+-]?0\.00%$/);
    await expect(value).not.toHaveText(/^--$/);
  }
});
