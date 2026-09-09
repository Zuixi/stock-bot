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
    const card = page.locator(".ant-card").filter({ hasText: "板块主力资金流" });
    await expect15s(card).toBeVisible();
    await card.locator(".ant-segmented-item").filter({ hasText: "概念" }).click();
    await expect15s(card.locator(".ant-segmented-item").filter({ hasText: "概念" })).toHaveClass(
      /ant-segmented-item-selected/,
    );
  });

  test("数据面：Tab 表格", async ({ page }) => {
    await page.goto("/market");
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
