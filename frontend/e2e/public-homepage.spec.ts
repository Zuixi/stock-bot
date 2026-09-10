import { expect, test } from "@playwright/test";

/**
 * 公开行情台首页 E2E。
 *
 * 首页产品决策为「免登录行情为主」：行情区块是主内容，营销区压缩到尾部。
 * 本文件按 Task 递进锁定契约：
 * - 1.4 导航锚点齐全 + 榜单区块骨架挂载
 * - 1.5 脉搏区（指数条 / 涨跌分布柱 / 家数汇总）与 11 桶完整性
 * - 1.6 榜单三 Tab 与 DeltaText 缺失值 `--` 契约
 */
const MOCK_SESSION_ANON = (page: import("@playwright/test").Page) =>
  page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "未登录或会话已过期" }),
    })
  );

test.describe("公开行情台首页", () => {
  test("首页可匿名访问：返回 200 且 H1 可见", async ({ page }) => {
    await MOCK_SESSION_ANON(page);

    const response = await page.goto("/");
    expect(response?.status()).toBe(200);

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });

  test("行情为主：导航锚点齐全且榜单区块骨架就位", async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.goto("/");
    for (const id of ["pulse", "rankings", "sectors", "money", "news"]) {
      await expect(page.locator(`nav a[href="#${id}"]`)).toBeVisible();
    }
    // 骨架阶段只断言榜单区块挂载；section-pulse 在 Task 1.5 包裹 SectionCard 后才存在
    await expect(page.getByTestId("section-rankings")).toBeVisible();
  });
});
