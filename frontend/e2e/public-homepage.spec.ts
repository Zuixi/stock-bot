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

/** 全 11 桶分布（顺序即后端契约 跌停 → 涨停），用于校验分桶求和完整性 */
const MOCK_DISTRIBUTION_11 = [
  { range: "跌停", count: 12 },
  { range: ">-7%", count: 30 },
  { range: "-5~-7%", count: 73 },
  { range: "-3~-5%", count: 336 },
  { range: "-1~-3%", count: 1839 },
  { range: "0~-1%", count: 1357 },
  { range: "0~1%", count: 949 },
  { range: "1~3%", count: 606 },
  { range: "3~5%", count: 174 },
  { range: ">5%", count: 113 },
  { range: "涨停", count: 61 },
];

/** down = 12+30+73+336+1839+1357 = 3647；up（含 0~1%）= 949+606+174+113+61 = 1903 */
const MOCK_DIST_DOWN = 3647;
const MOCK_DIST_UP = 1903;

const MOCK_INDICES_8 = Array.from({ length: 8 }, (_, i) => ({
  ts_code: i === 0 ? "000001.SH" : `MOCK${i}.SH`,
  name: i === 0 ? "上证指数" : `测试指数${i}`,
  market: "CN",
  region: "asia",
  price: 3000 + i,
  change: 1.5,
  pct_change: 0.5,
  spark: [],
  updated_at: "2026-09-11T10:00:00",
  source: "realtime",
}));

test.describe("公开行情台首页 · 脉搏区（Task 1.5）", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.route("**/api/v1/market/global-indices", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_INDICES_8),
      })
    );
    await page.route("**/api/v1/market/distribution", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(MOCK_DISTRIBUTION_11),
      })
    );
  });

  test("脉搏区：指数条 + 涨跌分布柱 + 家数汇总", async ({ page }) => {
    await page.goto("/#pulse");
    const pulse = page.getByTestId("section-pulse");
    await expect(pulse.locator(".index-ticker")).toHaveCount(8, { timeout: 15000 });
    await expect(pulse.locator(".distribution-bars")).toBeVisible();
    // 家数汇总：up 含 0~1%，down 含 0~-1%——两侧对称且穷尽 11 桶
    await expect(pulse.getByText(/上涨\s*1,903/)).toBeVisible();
    await expect(pulse.getByText(/下跌\s*3,647/)).toBeVisible();
  });

  test("涨跌分桶完整：上涨 + 下跌 = 11 桶总和（不丢 0~1% 桶）", async ({ page }) => {
    await page.goto("/#pulse");
    const pulse = page.getByTestId("section-pulse");
    await expect(pulse.locator(".distribution-bars")).toBeVisible({ timeout: 15000 });

    // 柱状图逐桶渲染，家数求和必须等于 11 桶总和
    const counts = await pulse.locator(".distribution-bars__count").allTextContents();
    expect(counts).toHaveLength(11);
    const sum = counts.reduce((s, c) => s + Number(c.replace(/[^\d]/g, "")), 0);
    expect(sum).toBe(MOCK_DIST_UP + MOCK_DIST_DOWN);
  });
});
