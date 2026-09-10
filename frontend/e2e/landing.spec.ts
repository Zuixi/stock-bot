import { test, expect as baseExpect } from "@playwright/test";

/**
 * 宣传页（Landing）E2E。
 * 路由策略契约：/ 为公开宣传页；已登录停留并换 CTA「进入工作台」，未登录显示「免费开始」。
 * 数据区块（脉搏卡/行业网格）以 route mock 驱动，保证断言确定性；接口异常走降级路径单测覆盖。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const MOCK_USER = {
  id: "u-landing-1",
  username: "landing_user",
  email: "landing@example.com",
  display_name: "Landing 用户",
  status: "active",
  roles: ["trader"],
  permissions: ["stocks:read"],
};

const MOCK_SESSION_OK = (page: import("@playwright/test").Page) =>
  page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(MOCK_USER),
    })
  );

const MOCK_SESSION_ANON = (page: import("@playwright/test").Page) =>
  page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "未登录或会话已过期" }),
    })
  );

/** 字段名对齐 shared/types MarketIndex（value/changePercent/tsCode） */
// global-indices 后端原始 payload（snake_case，与 /api/v1/market/global-indices 一致）
const MOCK_INDICES = [
  { ts_code: "000001.SH", name: "上证指数", market: "CN", region: "asia", price: 3951.51, change: 10.96, pct_change: 0.28, spark: [3900, 3920, 3951.51], updated_at: "2026-09-11T10:00:00", source: "realtime" },
  { ts_code: "399001.SZ", name: "深证成指", market: "CN", region: "asia", price: 13723.32, change: 20.11, pct_change: 0.15, spark: [13600, 13700, 13723.32], updated_at: "2026-09-11T10:00:00", source: "realtime" },
  { ts_code: "HSI", name: "恒生指数", market: "HK", region: "asia", price: 25274.96, change: -42.22, pct_change: -0.17, spark: [25300, 25280, 25274.96], updated_at: "2026-09-11T10:00:00", source: "realtime" },
];

test.describe("宣传页路由与品牌", () => {
  test("未登录：/ 渲染 StockBot 宣传页，Hero 与 CTA 正确", async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.goto("/");

    await expect(page.getByRole("heading", { level: 1, name: "把一个行业，研究透。" })).toBeVisible();
    // 信任行被渲染为三个独立元素，分别断言
    await expect(page.getByText("申万 31 个一级行业").first()).toBeVisible();
    await expect(page.getByText("5,500+ 只个股").first()).toBeVisible();
    await expect(page.getByText("多源交叉验证").first()).toBeVisible();

    // 主 CTA：button 实现，文案「免费开始」，点击跳 /login
    const cta = page.getByTestId("landing-cta");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("免费开始");
    await cta.click();
    await page.waitForURL(/\/login/);
  });

  test("未登录：/ 不再跳转 /market（停留宣传页）", async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.goto("/");
    await page.waitForTimeout(800);
    await expect(page).toHaveURL("/");
  });

  test("已登录：/ 停留宣传页且 CTA 换成「进入工作台」", async ({ page }) => {
    await MOCK_SESSION_OK(page);
    await page.goto("/");

    const cta = page.getByTestId("landing-cta");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("进入工作台");
    // Hero 与底部 CTA 同步
    await expect(page.getByTestId("landing-cta-hero")).toContainText("进入工作台");
    // 点击进入工作台
    await cta.click();
    await page.waitForURL(/\/market/);
  });
});

test.describe("宣传页数据区块", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.route("**/api/v1/market/global-indices", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_INDICES),
      })
    );
    await page.route("**/api/v1/market/distribution", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          { range: "1~3%", count: 80 }, { range: "3~5%", count: 30 }, { range: ">5%", count: 19 },
          { range: "涨停", count: 5 }, { range: "0~1%", count: 100 },
          { range: "0~-1%", count: 90 }, { range: "-1~-3%", count: 120 }, { range: "-3~-5%", count: 60 },
        ]),
      })
    );
    await page.route("**/api/v1/market/sw-industry/tree", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            code: "110000", name: "农林牧渔", stockCount: 95,
            children: [
              { code: "1101", name: "种植业", stockCount: 30, children: [] },
              { code: "1103", name: "养殖业", stockCount: 40, children: [] },
              { code: "1104", name: "饲料", stockCount: 25, children: [] },
            ],
          },
          { code: "220000", name: "基础化工", stockCount: 322, children: [{ code: "2201", name: "化学制品", stockCount: 100, children: [] }] },
          { code: "610000", name: "家用电器", stockCount: 78, children: [] },
        ]),
      })
    );
  });

  test("实时脉搏卡渲染指数与涨跌分布摘要", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("上证指数").first()).toBeVisible();
    await expect(page.getByText("+0.28%").first()).toBeVisible();
    // 上涨/下跌摘要（分桶口径：up=80+30+19+5=134，down=90+120+60=270）
    await expect(page.getByText(/上涨\s*134/).first()).toBeVisible();
    await expect(page.getByText(/下跌\s*270/).first()).toBeVisible();
  });

  test("数据覆盖矩阵含核心数据域与免费徽章", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("A股行情").first()).toBeVisible();
    await expect(page.getByText("行业产能指标").first()).toBeVisible();
    await expect(page.getByText("财务三表").first()).toBeVisible();
  });

  test("申万行业网格渲染并带个股数提示", async ({ page }) => {
    await page.goto("/");
    const tile = page.getByTitle(/95 只个股 · 3 个二级行业/);
    await expect(tile).toBeVisible();
    await expect(tile).toContainText("农林牧渔");
  });
});
