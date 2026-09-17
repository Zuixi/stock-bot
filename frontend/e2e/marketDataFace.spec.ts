import { expect, test } from "@playwright/test";

/**
 * 市场数据面 E2E（依赖运行中的 docker 栈，数据为实盘源）。
 * - 全球市场区块：亚洲/美洲 Tab 与指数徽章卡（30日 sparkline）
 * - 全球指数详情页：/index/N225 指数历史行情卡
 * - 板块主力资金流卡：行业/概念 Segmented 切换
 * - 数据面卡：龙虎榜表格列头 / 公告快讯 List
 * - 个股详情页：相关数据卡（回购视图表格）
 * 数据面为活栈采集（非交易时段资金流为空态属正常），用例断 UI 结构不断数值；
 * 数据缺失时按既有惯例 test.skip() 优雅跳过，勿删用例。
 */

const expect15s = expect.configure({ timeout: 15_000 });

test.describe("市场数据面", () => {
  test("全球市场：亚洲/美洲 Tab 与指数卡", async ({ page }) => {
    const resp = await page.request.get("/api/v1/market/global-indices");
    test.skip(!resp.ok(), "global-indices 端点不可用，跳过");
    const indices = await resp.json();
    test.skip(!Array.isArray(indices) || indices.length === 0, "全球指数数据为空，跳过");

    await page.goto("/market");
    await expect15s(page.getByRole("tab", { name: "亚洲" })).toBeVisible();
    await expect15s(page.getByText("日经225")).toBeVisible();
    await page.getByRole("tab", { name: "美洲" }).click();
    await expect15s(page.getByText("道琼斯")).toBeVisible();
    // antd Tabs 已激活的 pane 保持挂载（隐藏不卸载），断"不可见"而非"不在 DOM"
    await expect15s(page.getByText("日经225")).toBeHidden();
  });

  test("全球指数详情页", async ({ page }) => {
    await page.goto("/index/N225");
    // 指数数据缺失时页面渲染 404 Result —— 优雅跳过
    const notFound = page.getByText("未找到该指数");
    const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
    await expect15s(notFound.or(card).first()).toBeVisible();
    test.skip(await notFound.isVisible(), "指数 N225 数据缺失，跳过");
    await expect15s(card).toBeVisible();
  });

  test("板块资金流：行业/概念切换", async ({ page }) => {
    await page.goto("/market");
    // Stage C：资金流卡移入「资金流向」分类 Tab
    await page.getByRole("tab", { name: "资金流向" }).click();
    const card = page.locator(".ant-card").filter({ hasText: "板块主力资金流" });
    await expect15s(card).toBeVisible();
    await card.locator(".ant-segmented-item").filter({ hasText: "概念" }).click();
    await expect15s(card.locator(".ant-segmented-item").filter({ hasText: "概念" })).toHaveClass(
      /ant-segmented-item-selected/,
    );
  });

  test("数据面：Tab 表格", async ({ page }) => {
    await page.goto("/market");
    // Stage C：数据面迁移至同名分类 Tab 下（先切顶层 Tab 再点内层龙虎榜）
    await page.getByRole("tab", { name: "数据面" }).click();
    await page.getByRole("tab", { name: "龙虎榜", exact: true }).click();
    const board = page.locator(".ant-card").filter({ hasText: "数据面" });
    await expect15s(board.locator("thead th").filter({ hasText: "上榜原因" })).toBeVisible();
    await page.getByRole("tab", { name: "公告快讯" }).click();
    await expect15s(board.locator(".ant-list")).toBeVisible();
  });

  test("个股相关数据卡", async ({ page }) => {
    await page.goto("/stock/600519");
    // 个股不存在时页面渲染 404 Result —— 优雅跳过
    const notFound = page.getByText("未找到该股票");
    const card = page.locator(".ant-card").filter({ hasText: "相关数据" });
    await expect15s(notFound.or(card).first()).toBeVisible();
    test.skip(await notFound.isVisible(), "个股 600519 数据缺失，跳过");
    await expect15s(card).toBeVisible();
    await card.locator(".ant-segmented-item").filter({ hasText: "回购" }).click();
    await expect15s(card.locator("table")).toBeVisible();
  });
});

/**
 * Phase 1 Task 9：市场卡片必须自证「数据截至何时 + 什么口径」。
 *
 * 断言分两档：
 * - 活栈档（真实端点）：按日聚合卡显示「数据截至」+ 口径徽标（收盘/回落至/未完整三选一，
 *   避免把「今天恰好不完整」写成假红）；纯实时卡（A股核心指数）不得谎报日级口径。
 * - mock 档：回落（`as_of_quality=fallback`）与北向停更（`source_status=discontinued`）
 *   两个分支当日数据形态不保证命中，故用 route mock 固定。
 *
 * mock 载荷与真实端点**同键集**（best-practices：手写 mock 只锁得住前端自己的假设）：
 * 先实抓一份活响应，只覆写「口径」字段，条数/分桶/其余键全部来自真实快照。
 */
async function mockFallbackDistribution(page: import("@playwright/test").Page) {
  const resp = await page.request.get("/api/v1/market/distribution");
  const live = resp.ok() ? await resp.json() : { items: [] };
  await page.route("**/api/v1/market/distribution", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ...live,
        as_of: "2026-09-08",
        as_of_quality: "fallback",
        as_of_reason: "latest_day_incomplete",
      }),
    })
  );
}

/** 同上：北向信封同样实抓后只覆写停更标注，避免手写形状与后端漂移。 */
async function mockDiscontinuedNorthbound(page: import("@playwright/test").Page) {
  const resp = await page.request.get("/api/v1/market/northbound?days=30");
  const live = resp.ok() ? await resp.json() : { items: [] };
  await page.route("**/api/v1/market/northbound*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...live, as_of: "2026-09-07", stale_days: 10, source_status: "discontinued" }),
    })
  );
}

test.describe("市场卡片口径标注", () => {
  test("A股全景/资金流向卡片显示「数据截至」与口径徽标", async ({ page }) => {
    const resp = await page.request.get("/api/v1/market/distribution");
    test.skip(!resp.ok(), "distribution 端点不可用，跳过");
    const body = await resp.json();
    test.skip(!body?.as_of, "库内无行情（as_of 为空），跳过");

    await page.goto("/market");
    await page.getByRole("tab", { name: "A股全景" }).click();
    // 四张按日聚合卡（A股全景三张 + 资金流向的板块主力资金流）
    for (const title of ["A股涨跌分布", "板块热力图", "A股热门板块"]) {
      const card = page.locator(".ant-card").filter({ hasText: title });
      await expect15s(card.getByText(/数据截至/).first()).toBeVisible();
      await expect15s(card.getByText(/收盘|回落至|未完整/).first()).toBeVisible();
    }
    await page.getByRole("tab", { name: "资金流向" }).click();
    const moneyflow = page.locator(".ant-card").filter({ hasText: "板块主力资金流" });
    await expect15s(moneyflow.getByText(/数据截至/).first()).toBeVisible();
    // 快照回落口径：后端 stale_days 必须显示为「N 天前」
    await expect15s(moneyflow.getByText(/\d+ 天前/).first()).toBeVisible();
  });

  test("纯实时卡不谎报日级口径", async ({ page }) => {
    await page.goto("/market");
    const coreIndex = page.locator(".ant-card").filter({ hasText: "A股核心指数" });
    await expect15s(coreIndex).toBeVisible();
    await expect(coreIndex.getByText(/数据截至/)).toHaveCount(0);
  });

  test("回落口径卡片显示「回落至 {as_of}」", async ({ page }) => {
    await mockFallbackDistribution(page);
    await page.goto("/market");
    await page.getByRole("tab", { name: "A股全景" }).click();
    const card = page.locator(".ant-card").filter({ hasText: "A股涨跌分布" });
    await expect15s(card.getByText(/数据截至\s*9月8日/).first()).toBeVisible();
    await expect15s(card.getByText(/回落至\s*9月8日/).first()).toBeVisible();
  });

  test("北向停更分支显示「数据源已停更」", async ({ page }) => {
    await mockDiscontinuedNorthbound(page);
    await page.goto("/market");
    await page.getByRole("tab", { name: "资金流向" }).click();
    const card = page.locator(".ant-card").filter({ hasText: "北向资金" });
    await expect15s(card.getByText(/数据截至\s*9月7日/).first()).toBeVisible();
    await expect15s(card.getByText("数据源已停更").first()).toBeVisible();
    // stale_days=10 → 「10 天前」
    await expect15s(card.getByText(/10 天前/).first()).toBeVisible();
  });

  test("短线情绪三卡显示「数据截至」与口径徽标", async ({ page }) => {
    const resp = await page.request.get("/api/v1/market/limit-up-ladder");
    test.skip(!resp.ok(), "limit-up-ladder 端点不可用，跳过");
    const body = await resp.json();
    test.skip(!body?.as_of, "梯队无 as_of（库内无行情），跳过");

    await page.goto("/market");
    await page.getByRole("tab", { name: "短线情绪" }).click();
    for (const title of ["连板梯队", "申万三级最高板", "昨日涨停今日表现"]) {
      const card = page.getByTestId(`section-${title}`);
      await expect15s(card.getByText(/数据截至/).first()).toBeVisible();
      await expect15s(card.getByText(/收盘|回落至|未完整/).first()).toBeVisible();
    }
  });
});
