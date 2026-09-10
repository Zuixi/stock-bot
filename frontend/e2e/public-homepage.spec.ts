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

    // 汇总行的上涨/下跌必须与实际分桶一致，且两者之和 == 11 桶总和（漏桶即在此暴露）
    const [upText, downText] = await pulse
      .locator(".landing-pulse-summary b")
      .allTextContents();
    const up = Number(upText.replace(/[^\d]/g, ""));
    const down = Number(downText.replace(/[^\d]/g, ""));
    expect(up).toBe(MOCK_DIST_UP);
    expect(down).toBe(MOCK_DIST_DOWN);
    expect(up + down).toBe(sum);
    expect(sum).toBe(MOCK_DIST_UP + MOCK_DIST_DOWN);
  });

  test("分布返回空数组时走不可用占位，而非 上涨 0 · 下跌 0", async ({ page }) => {
    // 后注册的路由优先，覆盖 beforeEach 的 11 桶 mock
    await page.route("**/api/v1/market/distribution", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: "[]" })
    );
    await page.goto("/#pulse");
    const pulse = page.getByTestId("section-pulse");
    await expect(pulse.getByText("今日涨跌分布暂不可用")).toBeVisible({ timeout: 15000 });
    await expect(pulse.locator(".distribution-bars")).toHaveCount(0);
    await expect(pulse.locator(".landing-pulse-summary b")).toHaveCount(0);
  });
});

test.describe("公开行情台首页 · 榜单区（Task 1.6）", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
  });

  test("榜单区：三个 Tab 默认涨幅榜有数据", async ({ page }) => {
    await page.goto("/#rankings");
    const section = page.getByTestId("section-rankings");
    await expect(section.getByRole("tab", { name: "涨幅榜" })).toBeVisible();
    await expect(section.getByRole("tab", { name: "跌幅榜" })).toBeVisible();
    await expect(section.getByRole("tab", { name: "成交额榜" })).toBeVisible();
    await expect(section.locator(".datarow").first()).toBeVisible({ timeout: 15000 });
  });

  test("榜单剔除 null 涨幅行，缺失涨跌幅仍渲染 --（绝不渲染 0.00%）", async ({ page }) => {
    // 契约 §DeltaText：ChangePercent 缺失（次新股无行情）时必须显示 "--"
    await page.route("**/api/v1/exchanges/stocks/enriched*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            {
              symbol: "300750",
              name: "宁德时代",
              latest_price: 250.5,
              change_percent: null,
              amount: 1_234_567,
            },
            {
              symbol: "600519",
              name: "贵州茅台",
              latest_price: 1500,
              change_percent: 1.23,
              amount: 987_654,
            },
          ],
          total: 2,
          page: 1,
          page_size: 10,
        }),
      })
    );

    await page.goto("/#rankings");
    const section = page.getByTestId("section-rankings");

    // 涨幅榜按 changePercent 排序：null（次新股无行情）行被前端剔除，不占榜首
    await expect(section.locator(".datarow", { hasText: "贵州茅台" })).toBeVisible({
      timeout: 15000,
    });
    await expect(section.locator(".datarow", { hasText: "宁德时代" })).toHaveCount(0);

    // DeltaText 缺失值契约改在不受排序过滤影响的成交额榜断言（该行 amount 非空，故仍渲染）
    await section.getByRole("tab", { name: "成交额榜" }).click();
    const nullRow = section.locator(".datarow", { hasText: "宁德时代" });
    await expect(nullRow).toBeVisible({ timeout: 15000 });
    await expect(nullRow.locator(".delta")).toHaveText("--");
    await expect(section.getByText("0.00%")).toHaveCount(0);
  });

  test("接口降级独立：脉搏区两条查询全失败不影响榜单区渲染", async ({ page }) => {
    await page.route("**/api/v1/exchanges/stocks/enriched*", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify({
          items: [
            { symbol: "600519", name: "贵州茅台", latest_price: 1500, change_percent: 1.23, amount: 987_654 },
          ],
          total: 1,
          page: 1,
          page_size: 10,
        }),
      })
    );
    await page.route("**/api/v1/market/global-indices", (route) => route.abort());
    await page.route("**/api/v1/market/distribution", (route) => route.abort());

    await page.goto("/#rankings");
    await expect(page.getByTestId("section-pulse")).toBeVisible();
    await expect(
      page.getByTestId("section-rankings").locator(".datarow").first()
    ).toBeVisible({ timeout: 15000 });
  });
});
