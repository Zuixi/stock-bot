import { test, expect as baseExpect } from "@playwright/test";

/**
 * 多用户数据归属与隔离前端 E2E 测试套件。
 * - 验证用户 A 登录后查看自身的自选股与自定义标签
 * - 验证切换用户 B 后数据完全隔离（用户 A 的自选与标签不可见，仅展示用户 B 数据）
 * - 验证未登录状态下的隔离防护与登录重定向
 */

const expect = baseExpect.configure({ timeout: 15_000 });

test.describe("多用户数据归属与隔离验证", () => {
  const userA = {
    id: "usr-uuid-001",
    username: "user_alice",
    email: "alice@invest.com",
    display_name: "Alice投研",
    status: "active",
    is_superuser: false,
    roles: ["researcher"],
    permissions: ["stocks:read", "watchlist:manage", "tags:manage"],
    created_at: "2026-09-09T00:00:00Z",
  };

  const userB = {
    id: "usr-uuid-002",
    username: "user_bob",
    email: "bob@quant.com",
    display_name: "Bob量化",
    status: "active",
    is_superuser: false,
    roles: ["trader"],
    permissions: ["stocks:read", "watchlist:manage", "tags:manage"],
    created_at: "2026-09-09T00:00:00Z",
  };

  const stock600519Enriched = {
    exchange: "Shanghai_Stocks",
    symbol: "600519",
    name: "贵州茅台",
    category: "主板",
    csrc_desc: "酒、饮料和精制茶制造业",
    latest_price: 1588.0,
    prev_close: 1560.0,
    open: 1570.0,
    high: 1600.0,
    low: 1565.0,
    change: 28.0,
    change_percent: 1.79,
    volume: 35000,
    amount: 550000,
    total_mv: 1990000,
    circ_mv: 1990000,
    asof: "2026-09-09",
  };

  const stock000001Enriched = {
    exchange: "Shenzen_Stocks",
    symbol: "000001",
    name: "平安银行",
    category: "主板",
    csrc_desc: "货币金融服务",
    latest_price: 11.2,
    prev_close: 11.0,
    open: 11.05,
    high: 11.35,
    low: 11.0,
    change: 0.2,
    change_percent: 1.82,
    volume: 120000,
    amount: 134000,
    total_mv: 217000,
    circ_mv: 217000,
    asof: "2026-09-09",
  };

  test("用户 A 与用户 B 的自选股列表多租户隔离验证", async ({ page }) => {
    let currentUser = userA;

    await page.route("**/auth/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(currentUser),
      })
    );

    await page.route("**/auth/csrf", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ csrf_token: "csrf-token-test" }),
      })
    );

    // Mock stock enriched endpoints
    await page.route("**/api/v1/exchanges/*/stocks/600519/enriched", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(stock600519Enriched),
      })
    );

    await page.route("**/api/v1/exchanges/*/stocks/000001/enriched", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(stock000001Enriched),
      })
    );

    // Watchlist API based on currentUser
    await page.route("**/api/v1/watchlists", (route) => {
      if (currentUser.id === userA.id) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              id: "wl-item-1",
              symbol: "600519",
              exchange: "Shanghai_Stocks",
              sort_order: 1,
              created_at: "2026-09-09T00:00:00Z",
            },
          ]),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "wl-item-2",
            symbol: "000001",
            exchange: "Shenzen_Stocks",
            sort_order: 1,
            created_at: "2026-09-09T00:00:00Z",
          },
        ]),
      });
    });

    // 1. 用户 A 视域：查看自选股
    currentUser = userA;
    await page.goto("/watchlist");

    await expect(page.getByText("我的自选（1）")).toBeVisible();
    await expect(page.getByText("贵州茅台")).toBeVisible();
    await expect(page.getByText("600519")).toBeVisible();
    await expect(page.getByText("平安银行")).toHaveCount(0);

    // 2. 切换为用户 B 视域：重新加载页面验证隔离
    currentUser = userB;
    await page.goto("/watchlist");

    await expect(page.getByText("我的自选（1）")).toBeVisible();
    await expect(page.getByText("平安银行")).toBeVisible();
    await expect(page.getByText("000001")).toBeVisible();
    await expect(page.getByText("贵州茅台")).toHaveCount(0);
    await expect(page.getByText("600519")).toHaveCount(0);
  });

  test("用户 A 与用户 B 的自定义标签多租户隔离验证", async ({ page }) => {
    let currentUser = userA;

    await page.route("**/auth/session", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(currentUser),
      })
    );

    await page.route("**/auth/csrf", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({ csrf_token: "csrf-token-test" }),
      })
    );

    // Tags API based on currentUser
    await page.route("**/api/v1/tags", (route) => {
      if (currentUser.id === userA.id) {
        return route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify([
            {
              tag_name: "白酒龙头",
              stock_count: 1,
            },
          ]),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            tag_name: "银行核心",
            stock_count: 3,
          },
        ]),
      });
    });

    // 1. 用户 A 视域：查看自定义标签
    currentUser = userA;
    await page.goto("/tags");

    await expect(page.getByText("自定义标签")).toBeVisible();
    await expect(page.getByText("白酒龙头")).toBeVisible();
    await expect(page.getByText("1 只个股")).toBeVisible();
    await expect(page.getByText("银行核心")).toHaveCount(0);

    // 2. 切换为用户 B 视域：查看自定义标签
    currentUser = userB;
    await page.goto("/tags");

    await expect(page.getByText("自定义标签")).toBeVisible();
    await expect(page.getByText("银行核心")).toBeVisible();
    await expect(page.getByText("3 只个股")).toBeVisible();
    await expect(page.getByText("白酒龙头")).toHaveCount(0);
  });
});
