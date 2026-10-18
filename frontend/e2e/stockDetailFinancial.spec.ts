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
 * 本用例钉住两件事：
 * 1. **数值上屏**：ROE 格子是数字（不是 `--`），且带报告期标签；
 * 2. **切 tab 零新请求**：概览与财务 tab 共用 query key `["financial-summary", exchange, symbol]`，
 *    点「财务」后 `/financial-summary` 的请求数必须仍是 1（缓存命中，不是重新拉一遍）。
 *
 * 断言纪律（与 stockDetailRequests.spec.ts 同）：`page.on("request")` 必须在 `goto`
 * **之前**注册，否则首个请求会漏记；锚点用页面上真实出现的卡片/数值，不是固定 sleep。
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

test("概览「成长与盈利」卡：ROE 显示真实数字，切财务 tab 不重复请求 financial-summary", async ({
  page,
}) => {
  // ⚠️ 监听必须在 goto 之前：首个请求在页面挂载时立刻发出，晚注册就漏记
  const summaryRequests = recordSummaryRequests(page);

  await page.goto("/stock/600519");

  const card = page.locator(".ant-card").filter({ hasText: "成长与盈利" });
  await expect(card).toBeVisible();

  // ---- ROE：必须是数字（回归点：改造前这里永远是 `--`） ----
  const roeCell = metricCell(card, "ROE");
  await expect(roeCell).toBeVisible();
  const roeValue = metricValue(roeCell);
  // 数字形态：`17.95%`（不带正负号 —— ROE 是存量比率，不是"变化"，见 FundamentalCards 注释）
  await expect(roeValue).toHaveText(/\d/);
  await expect(roeValue).not.toHaveText(/^--$/);
  await expect(roeValue).toHaveText(/^-?\d+(\.\d+)?%$/);

  // 报告期标签：有值时必须挂在格子下面（`summary.latest_period`，形如 2026-06-30）
  await expect(roeCell).toContainText(/\d{4}-\d{2}-\d{2}/);

  // ---- 营收同比：同样要求数值；若该股票确实无此指标则容忍 `--`（缺失 ≠ 0，不得渲染 0） ----
  const revenueCell = metricCell(card, "营收同比");
  await expect(revenueCell).toBeVisible();
  const revenueText = (await metricValue(revenueCell).innerText()).trim();
  if (revenueText !== "--") {
    // 同比是变化量：走 ChangeText，带符号（`+1.47%` / `-5.00%`）
    expect(revenueText).toMatch(/^[+-]?\d+(\.\d+)?%$/);
  }

  // 净利润同比：600519 当前为负增长（-1.95%），但仍必须是数字；无数据时容忍 `--`
  const profitText = (await metricValue(metricCell(card, "净利润同比")).innerText()).trim();
  if (profitText !== "--") {
    expect(profitText).toMatch(/^[+-]?\d+(\.\d+)?%$/);
  }

  // ---- 请求数：概览加载完成后 `/financial-summary` 恰好 1 次 ----
  // 先让在途请求落地，再统计（避免"断言时还没发出来"造成假绿）
  await page.waitForLoadState("networkidle").catch(() => {});
  expect(
    summaryRequests,
    `概览页应只请求 1 次 financial-summary，实际 ${summaryRequests.length} 次：\n${summaryRequests.join("\n")}`,
  ).toHaveLength(1);

  // ---- 切「财务」tab：同 query key ⇒ 命中缓存，请求数**仍是 1** ----
  await page.getByRole("tab", { name: "财务", exact: true }).click();
  // 锚点：财务 tab 自己发出的另外两个端点先落地，证明 tab 已真正挂载（而不是只切了个高亮）
  await expect(page.locator(".ant-card").filter({ hasText: "财务摘要" })).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(
    summaryRequests,
    `切到财务 tab 后不应再请求 financial-summary（应为同一 query key 命中缓存），` +
      `实际 ${summaryRequests.length} 次：\n${summaryRequests.join("\n")}`,
  ).toHaveLength(1);
});

test("财务摘要缺失时三张卡显示 --（不是 0），且不挂报告期与质量徽标", async ({ page }) => {
  // 用 route 造「有该股票、但指标为 null」的响应：fixture 里未必存在这种票，
  // 而「缺失 ≠ 0」是本卡最容易被写坏的口径（默认 0 会把"没有数据"说成"零增长"）。
  await page.route("**/financial-summary*", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        exchange: "Shanghai_Stocks",
        symbol: "600519",
        name: "贵州茅台",
        latest_period: null,
        report_type: null,
        ann_date: null,
        as_of: null,
        source: null,
        // 故意只留一个 null 指标：另外两个键直接缺席（后端可能整键不返回）
        metrics: { roe: { value: null, unit: "%", quality: null, source: null } },
      }),
    });
  });

  await page.goto("/stock/600519");

  const card = page.locator(".ant-card").filter({ hasText: "成长与盈利" });
  await expect(card).toBeVisible();

  for (const label of ["ROE", "营收同比", "净利润同比"]) {
    const cell = metricCell(card, label);
    const value = metricValue(cell);
    await expect(value, `${label} 缺失时应渲染 --`).toHaveText("--");
    // 缺失不得被写成 0 / 0.00%
    await expect(value).not.toHaveText(/^0(\.0+)?%?$/);
    // 无值 ⇒ 不显示报告期、不显示质量徽标（`--` 旁边挂"财报"是自相矛盾的）
    await expect(cell, `${label} 缺失时不应有报告期/质量徽标`).not.toContainText(/\d{4}-\d{2}-\d{2}/);
    await expect(cell).not.toContainText(/财报|估算/);
  }
});
