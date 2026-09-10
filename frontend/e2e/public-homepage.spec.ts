import { expect, test } from "@playwright/test";

/**
 * 公开行情台首页 E2E。
 *
 * 本批次仅锁定「登录无关的首页可访问」这一最小冒烟契约：
 * `/` 返回 200 且一级标题可见。后续 Task 1.7+ 逐步扩展行情区块断言
 * （DeltaText 缺失值 `--` 契约在首页挂载首个消费方后再补）。
 */
test.describe("公开行情台首页", () => {
  test("首页可匿名访问：返回 200 且 H1 可见", async ({ page }) => {
    await page.route("**/auth/session", (route) =>
      route.fulfill({
        status: 401,
        contentType: "application/json",
        body: JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "未登录或会话已过期" }),
      })
    );

    const response = await page.goto("/");
    expect(response?.status()).toBe(200);

    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  });
});
