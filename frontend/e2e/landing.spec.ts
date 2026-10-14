import { test, expect as baseExpect } from "@playwright/test";

/**
 * 宣传页（Landing）E2E。
 * 路由策略契约：/ 为公开宣传页；已登录停留并换 CTA「进入工作台」，未登录显示「登录」（ADR 0010 邀请制）。
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
  // 涨跌幅缺失（EOD 兜底且 spark 不足时 pct_change 为 null）——须显示 "--" 而非 "0.00%"
  { ts_code: "000300.SH", name: "沪深300", market: "CN", region: "asia", price: 4548.39, change: null, pct_change: null, spark: [], updated_at: "2026-09-11T10:00:00", source: "eod" },
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

    // 主 CTA：button 实现，文案「登录」（邀请制口径，ADR 0010），点击跳 /login
    const cta = page.getByTestId("landing-cta");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("登录");
    await cta.click();
    await page.waitForURL(/\/login/);
  });

  test("未登录：/ 不再跳转 /market（停留宣传页）", async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.goto("/");
    // 以 network-idle 为界（而非固定 sleep）：若发生重定向，导航请求已在 idle 前发出。
    // 固定 800ms 会让更慢的重定向漏网，故改为有界等待 + 稳定 URL 断言。
    await page.waitForLoadState("networkidle");
    expect(new URL(page.url()).pathname).toBe("/");
  });

  test("已登录：/ 停留宣传页且 CTA 换成「进入工作台」", async ({ page }) => {
    await MOCK_SESSION_OK(page);
    await page.goto("/");

    const cta = page.getByTestId("landing-cta");
    await expect(cta).toBeVisible();
    await expect(cta).toContainText("进入工作台");
    // 已登录不得再出现「登录」入口：CTA 已切「进入工作台」，旧实现旁边仍无条件渲染
    // 登录按钮，导致同一导航栏同时给出登录与进入工作台两个互斥动作。
    await expect(page.getByRole("button", { name: "登录" })).toHaveCount(0);
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
    // Task 2 起 `/market/distribution` 返回 `{as_of, as_of_quality, as_of_reason, items}` 信封
    await page.route("**/api/v1/market/distribution", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          as_of: "2026-09-11",
          as_of_quality: "complete",
          as_of_reason: null,
          items: [
            { range: "1~3%", count: 80 }, { range: "3~5%", count: 30 }, { range: ">5%", count: 19 },
            { range: "涨停", count: 5 }, { range: "0~1%", count: 100 },
            { range: "0~-1%", count: 90 }, { range: "-1~-3%", count: 120 }, { range: "-3~-5%", count: 60 },
          ],
        }),
      })
    );
    await page.route("**/api/v1/market/sw-industry/tree", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            // officialCount 才是显示口径（与下钻页一致）；customCount 只作「+N 自定义」标注。
            // 农林牧渔特意给 95 + 7：断言格子显示的是 **95**（官方），不是相加后的 102。
            code: "110000", name: "农林牧渔", stockCount: 102, officialCount: 95, customCount: 7,
            children: [
              { code: "1101", name: "种植业", stockCount: 30, officialCount: 30, customCount: 0, children: [] },
              { code: "1103", name: "养殖业", stockCount: 40, officialCount: 40, customCount: 0, children: [] },
              { code: "1104", name: "饲料", stockCount: 25, officialCount: 25, customCount: 0, children: [] },
            ],
          },
          { code: "220000", name: "基础化工", stockCount: 322, officialCount: 322, customCount: 0, children: [{ code: "2201", name: "化学制品", stockCount: 100, officialCount: 100, customCount: 0, children: [] }] },
          { code: "610000", name: "家用电器", stockCount: 78, officialCount: 78, customCount: 0, children: [] },
        ]),
      })
    );
  });

  test("实时脉搏卡渲染指数与涨跌分布摘要", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("上证指数").first()).toBeVisible();
    await expect(page.getByText("+0.28%").first()).toBeVisible();
    // 上涨/下跌摘要（分桶口径对称且穷尽：up 含 0~1% → 100+80+30+19+5=234，down=90+120+60=270）
    await expect(page.getByText(/上涨\s*234/).first()).toBeVisible();
    await expect(page.getByText(/下跌\s*270/).first()).toBeVisible();
  });

  test("缺失涨跌幅的指数显示 -- 而非 0.00%", async ({ page }) => {
    await page.goto("/");
    const card = page.locator(".landing-ticker", { hasText: "沪深300" });
    await expect(card.locator(".landing-ticker-price")).toHaveText("4,548.39");
    await expect(card.locator(".landing-ticker-chip")).toHaveText("--");
  });

  test("申万行业网格渲染并带个股数提示", async ({ page }) => {
    await page.goto("/");
    const tile = page.getByTitle(/95 只个股 · 3 个二级行业/);
    await expect(tile).toBeVisible();
    await expect(tile).toContainText("农林牧渔");
  });

  // 口径守护（本次裁决 A）：格子与卡片显示**官方成分数**（SCD2，与下钻页逐节点一致），
  // 用户自定义标签只作「+N 自定义」标注，**绝不相加后当官方口径**。
  // mock 特意给 95 官方 + 7 自定义 = stockCount 102 ⇒ 若哪天有人退回用 stockCount，本断言转红。
  test("申万行业网格显示官方成分数，自定义标签只作标注不相加", async ({ page }) => {
    await page.goto("/");
    const count = page.getByTitle(/95 只个股 · 3 个二级行业/).locator(".sw-count");
    await expect(count).toContainText("95 只");
    await expect(count).not.toContainText("102");
    await expect(page.locator(".sw-count-extra").first()).toHaveText("+7 自定义");
  });

  // 回归守护：格子此前是个纯 `<div>`，而 `.sw-cell:hover` 已有上浮反馈
  // ⇒ **看起来能点、点了没反应**。原测试只验渲染不验交互，因此漏过。
  // 现在断言它是真 `<Link>`（有 href ⇒ 可中键新开、可键盘触发）且点击真的换页。
  test("申万行业网格的格子是链接且能跳到一级行业下钻页", async ({ page }) => {
    await page.goto("/");
    const tile = page.getByTitle(/95 只个股 · 3 个二级行业/);
    await expect(tile).toHaveAttribute("href", "/market/industry/110000");
    await tile.click();
    await expect(page).toHaveURL(/\/market\/industry\/110000$/);
  });
});
