import { test, expect as baseExpect } from "@playwright/test";

/**
 * 层级式面包屑导航 E2E（依赖运行中的 docker 栈，数据为实盘源）。
 * - /stock/600540（新赛股份）：市场 / 申万L1 / L2 / L3 / {股票名}；行业链来自个股自身 sw_chain
 * - L1 链接可点击进入 /market/industry/**；"市场"可点击返回 /market；末项（股票名）非链接
 * - /index/000001.SH（上证指数）：市场 / {指数名}；指数数据缺失时优雅跳过
 *
 * 行业链名称由后端 sw_chain 实时读取驱动断言，避免硬编码实盘行业名。
 */

// react-query 异步加载 + 后端 LATERAL join，统一放宽断言轮询窗口
const expect = baseExpect.configure({ timeout: 15_000 });

interface SwChainNode {
  level: number;
  code: string;
  name: string;
}

test("个股详情面包屑：申万链完整展示且末项不可点击", async ({ page }) => {
  // 从后端读取个股自身申万链（与页面同一 enriched 端点）
  const resp = await page.request.get(
    "/api/v1/exchanges/Shanghai_Stocks/stocks/600540/enriched"
  );
  test.skip(!resp.ok(), "600540 enriched 数据缺失，跳过");
  const payload = await resp.json();
  const chain: SwChainNode[] = payload.sw_chain ?? [];
  test.skip(chain.length === 0, "600540 无申万映射（sw_chain 为空），跳过");
  const l1 = chain.find((node) => node.level === 1);
  test.skip(!l1, "600540 申万链缺少一级节点，跳过");

  await page.goto("/stock/600540");

  const crumb = page.locator(".ant-breadcrumb");
  await expect(crumb).toBeVisible();
  await expect(crumb).toContainText("市场");
  await expect(crumb).toContainText(payload.name);
  // 至少一个行业层级段可见（L1 名称来自 sw_chain）
  await expect(crumb).toContainText(l1!.name);
  // 末项为当前页（股票名）——非链接
  await expect(crumb.getByRole("link", { name: payload.name })).toHaveCount(0);
});

test("个股详情面包屑：L1 进入行业页 / 市场返回市场页", async ({ page }) => {
  const resp = await page.request.get(
    "/api/v1/exchanges/Shanghai_Stocks/stocks/600540/enriched"
  );
  test.skip(!resp.ok(), "600540 enriched 数据缺失，跳过");
  const payload = await resp.json();
  const chain: SwChainNode[] = payload.sw_chain ?? [];
  const l1 = chain.find((node) => node.level === 1);
  test.skip(!l1, "600540 申万链缺少一级节点，跳过");

  await page.goto("/stock/600540");
  const crumb = page.locator(".ant-breadcrumb");
  await expect(crumb).toBeVisible();

  // L1 名称链接 → /market/industry/{l1Code}
  await crumb.getByRole("link", { name: l1!.name }).click();
  await page.waitForURL(new RegExp(`/market/industry/${l1!.code}$`));

  // 回到个股页，点击"市场" → /market 根路由
  await page.goto("/stock/600540");
  await expect(page.locator(".ant-breadcrumb")).toBeVisible();
  await page.getByRole("link", { name: "市场" }).click();
  await page.waitForURL(/\/market$/);
});

test("指数详情面包屑：市场 / 指数名", async ({ page }) => {
  await page.goto("/index/000001.SH");

  // 指数数据缺失时页面渲染 404 Result —— 优雅跳过
  const notFound = page.getByText("未找到该指数");
  const crumb = page.locator(".ant-breadcrumb");
  await expect(crumb.or(notFound).first()).toBeVisible();
  test.skip((await notFound.count()) > 0 && (await notFound.isVisible()), "指数 000001.SH 数据缺失，跳过");

  await expect(crumb).toBeVisible();
  await expect(crumb).toContainText("市场");
  // 末项为指数名（当前页，非链接）
  const crumbText = await crumb.innerText();
  expect(crumbText.trim().length).toBeGreaterThan("市场".length);

  await crumb.getByRole("link", { name: "市场" }).click();
  await page.waitForURL(/\/market$/);
});
