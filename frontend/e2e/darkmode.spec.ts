import { test, expect as baseExpect } from "@playwright/test";

/**
 * 明暗双主题 E2E（TradingView 配色契约）。
 * - 切换按钮切换 html[data-theme]，页面底色随之变化
 * - localStorage('stockbot-theme') 持久化，刷新后保持
 * - 业务页（/market）暗色下无白底残留
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const MOCK_SESSION_OK = (page: import("@playwright/test").Page) =>
  page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "u-dark-1", username: "dark_user", email: "dark@example.com",
        display_name: "Dark 用户", status: "active", roles: ["trader"], permissions: [],
      }),
    })
  );

test.describe("明暗主题切换", () => {
  test.use({ colorScheme: "light" });

  test("默认浅色；点击切换到暗色且底色变化、刷新后保持", async ({ page }) => {
    // 不用 addInitScript 清 storage（与 expect 断言器存在交互异常），改为加载后清理再刷新
    await page.goto("/");
    await page.evaluate(() => localStorage.removeItem("stockbot-theme"));
    await page.reload();

    // 默认浅色
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

    // 等待导航树真正可交互（lazy chunk + 会话初始化会重挂载导航）
    await expect(page.getByRole("navigation", { name: "宣传页锚点导航" })).toBeVisible();
    await expect(page.getByTestId("theme-toggle").first()).toBeEnabled();

    // 会话 401 结算时导航可能重挂载，固定延时仍可能踩进竞态窗口——带重试点击直至生效
    const toggle = page.getByTestId("theme-toggle").first();
    for (let i = 0; i < 5; i++) {
      if ((await page.locator("html").getAttribute("data-theme")) === "dark") break;
      await toggle.click({ timeout: 2000 }).catch(() => {});
      await page.waitForTimeout(400);
    }
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    // TV 暗色底 #131722 = rgb(19, 23, 34)；body 有 0.2s 背景过渡，用 poll 等待过渡完成
    await expect
      .poll(async () => page.evaluate(() => getComputedStyle(document.body).backgroundColor))
      .toBe("rgb(19, 23, 34)");

    // 刷新后保持暗色（localStorage 持久化）
    await page.reload();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  });

  test("再点一次回到浅色并持久化", async ({ page }) => {
    await page.addInitScript(() => localStorage.setItem("stockbot-theme", "dark"));
    await page.goto("/");
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    await page.getByTestId("theme-toggle").first().click();
    await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
    const stored = await page.evaluate(() => localStorage.getItem("stockbot-theme"));
    expect(stored).toBe("light");
  });

  test("业务页（/market）暗色下页面与卡片无白底残留", async ({ page }) => {
    await MOCK_SESSION_OK(page);
    // 数据接口 mock 为空集合即可——本用例断言主题而非数据
    await page.route("**/api/v1/**", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
    await page.route("**/api/v1/market/indices", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );

    await page.addInitScript(() => localStorage.setItem("stockbot-theme", "dark"));
    await page.goto("/market");

    // Tab 骨架就位
    await expect(page.getByTestId("market-tabs")).toBeVisible();

    // 页面底色 = TV 暗色
    const pageBg = await page.evaluate(() => getComputedStyle(document.body).backgroundColor);
    expect(pageBg).toBe("rgb(19, 23, 34)");

    // 抽样已渲染卡片：暗色下不允许出现纯白背景（rgb(255,255,255)）
    const whiteCards = await page.evaluate(() => {
      const bad: string[] = [];
      document.querySelectorAll<HTMLElement>(".ant-card").forEach((el) => {
        const bg = getComputedStyle(el).backgroundColor;
        if (bg === "rgb(255, 255, 255)") bad.push(el.className.slice(0, 40));
      });
      return bad;
    });
    expect(whiteCards).toEqual([]);
  });
});
