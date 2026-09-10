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

const MOCK_SECTORS = [
  { name: "船舶", changePercent: 4.46, totalMarketCap: 1.14e10, stockCount: 11, topStocks: [] },
  { name: "水运", changePercent: 3.62, totalMarketCap: 1.36e10, stockCount: 19, topStocks: [] },
  { name: "煤炭开采", changePercent: 3.47, totalMarketCap: 1.45e10, stockCount: 25, topStocks: [] },
  { name: "焦炭加工", changePercent: 3.13, totalMarketCap: 2.31e9, stockCount: 7, topStocks: [] },
  { name: "渔业", changePercent: 2.82, totalMarketCap: 2.58e9, stockCount: 7, topStocks: [] },
  { name: "黄金", changePercent: 2.24, totalMarketCap: 1.7e10, stockCount: 10, topStocks: [] },
  { name: "港口", changePercent: 2.08, totalMarketCap: 3.5e9, stockCount: 16, topStocks: [] },
  { name: "铜", changePercent: 2.01, totalMarketCap: 3.18e10, stockCount: 18, topStocks: [] },
  { name: "水力发电", changePercent: 1.68, totalMarketCap: 5.68e9, stockCount: 20, topStocks: [] },
];

const MOCK_CAPITAL_FLOW = [
  { name: "元器件", inflow: 1509.75, outflow: -724.07 },
  { name: "半导体", inflow: 443.88, outflow: -1357.9 },
  { name: "通信设备", inflow: 1140.21, outflow: -569.02 },
  { name: "电气设备", inflow: 691.36, outflow: -405.52 },
  { name: "专用机械", inflow: 498.27, outflow: -473.62 },
  { name: "化工原料", inflow: 502.98, outflow: -246.72 },
  { name: "软件服务", inflow: 105.41, outflow: -437.9 },
  { name: "小金属", inflow: 340.59, outflow: -102.47 },
  { name: "汽车配件", inflow: 164.78, outflow: -243.14 },
];

const MOCK_MONEYFLOW = {
  today: {
    total: {
      amount: 1_647_147_829_484.9,
      main_net: -31_581_126_656,
      super_large_net: -16_085_057_536,
      large_net: -15_496_069_120,
      mid_net: 2_024_771_584,
      small_net: 29_556_350_976,
    },
    markets: [
      { code: "000001", name: "上证指数", main_net: -15_583_944_704, super_large_net: -7_581_204_480, large_net: -8_002_740_224, mid_net: 1_721_257_984, small_net: 13_862_682_624, main_ratio: -2 },
    ],
  },
  history: [
    { date: "2026-09-10", main_net: -20_000_000_000, super_large_net: -9e9, large_net: -1.1e10, mid_net: 1e9, small_net: 1.9e10, main_ratio: -1.2, close: 3900, pct_change: -0.5, amount: 1.5e12 },
    { date: "2026-09-11", main_net: -31_581_126_656, super_large_net: -1.6e10, large_net: -1.5e10, mid_net: 2e9, small_net: 2.9e10, main_ratio: -2, close: 3951, pct_change: 0.28, amount: 1.6e12 },
  ],
};

const ANNOUNCE_NOW = new Date();
const TWO_HOURS_AGO = new Date(ANNOUNCE_NOW.getTime() - 2 * 3600 * 1000).toISOString();
const THREE_HOURS_AGO = new Date(ANNOUNCE_NOW.getTime() - 3 * 3600 * 1000).toISOString();

const MOCK_ANNOUNCEMENTS = [
  { announcement_id: "a1", sec_code: "600519", sec_name: "贵州茅台", title: "2026 年半年度报告", announce_time: TWO_HOURS_AGO, category: "report", pdf_url: "http://example.com/a1.pdf" },
  { announcement_id: "a2", sec_code: "000001", sec_name: "平安银行", title: "2026 年第三季度报告", announce_time: THREE_HOURS_AGO, category: "report", pdf_url: "http://example.com/a2.pdf" },
  { announcement_id: "a3", sec_code: "300750", sec_name: "宁德时代", title: "关于向特定对象发行股票的公告", announce_time: TWO_HOURS_AGO, category: "event", pdf_url: "http://example.com/a3.pdf" },
  { announcement_id: "a4", sec_code: "601318", sec_name: "中国平安", title: "关于回购股份进展的公告", announce_time: THREE_HOURS_AGO, category: "event", pdf_url: "http://example.com/a4.pdf" },
];

test.describe("公开行情台首页 · 快讯区（Task 1.9）", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.route("**/api/v1/market/announcements*", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_ANNOUNCEMENTS) })
    );
  });

  test("快讯区：公告 Tab 可见且有条目", async ({ page }) => {
    await page.goto("/#news");
    const section = page.getByTestId("section-news");
    await expect(section).toBeVisible();
    await expect(section.getByRole("tab", { name: /财报|公告/ }).first()).toBeVisible();
    await expect(section.getByRole("tab", { name: "重大事项" })).toBeVisible();
    // 默认财报页：只呈现 report 条目，带相对时间与来源署名
    await expect(section.getByText("2026 年半年度报告")).toBeVisible({ timeout: 15000 });
    await expect(section.getByText("2小时前")).toBeVisible();
    await expect(section.getByText("巨潮").first()).toBeVisible();
    await expect(section.getByText("关于向特定对象发行股票的公告")).toHaveCount(0);
  });

  test("快讯区：切到重大事项 Tab 呈现 event 条目", async ({ page }) => {
    await page.goto("/#news");
    const section = page.getByTestId("section-news");
    await section.getByRole("tab", { name: "重大事项" }).click();
    await expect(section.getByText("关于向特定对象发行股票的公告")).toBeVisible({ timeout: 15000 });
    await expect(section.getByText("2026 年半年度报告")).toHaveCount(0);
  });
});

test.describe("公开行情台首页 · 资金区（Task 1.8）", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.route("**/api/v1/market/market-moneyflow", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_MONEYFLOW) })
    );
  });

  test("资金区：大盘资金流卡片可见（无北向卡）", async ({ page }) => {
    await page.goto("/#money");
    const section = page.getByTestId("section-money");
    await expect(section).toBeVisible();
    await expect(section.getByText("大盘资金流", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(section.getByText("今日主力净流入")).toBeVisible();
    // 北向数据源断流（northbound_daily 无行），资金区不得渲染北向卡或以本地序列替补
    await expect(section.getByText("北向")).toHaveCount(0);
  });

  test("资金区降级：接口挂掉显示占位，不白屏", async ({ page }) => {
    await page.route("**/api/v1/market/market-moneyflow", (route) => route.abort());
    await page.goto("/#money");
    await expect(page.getByTestId("section-money")).toBeVisible();
    // 卡壳仍在、走占位文案，不整区白屏
    await expect(page.getByTestId("section-money").getByText("大盘资金流", { exact: true })).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId("section-money").getByText("暂无大盘资金流数据（盘后自动更新）")).toBeVisible();
  });
});

test.describe("公开行情台首页 · 板块区（Task 1.7）", () => {
  test.beforeEach(async ({ page }) => {
    await MOCK_SESSION_ANON(page);
    await page.route("**/api/v1/market/sectors", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_SECTORS) })
    );
    await page.route("**/api/v1/market/capital-flow", (route) =>
      route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(MOCK_CAPITAL_FLOW) })
    );
  });

  test("板块区：行业涨跌 + 资金流 + 口径标注", async ({ page }) => {
    await page.goto("/#sectors");
    const section = page.getByTestId("section-sectors");
    await expect(section).toBeVisible();
    // 口径必须显式可见：左列证监会（临时口径），右列近似资金流口径（非真实主力净流入）
    await expect(section.getByText("行业口径：证监会")).toBeVisible();
    await expect(section.getByText("近似口径")).toBeVisible();
    // 左列行业涨跌（复用 DataRow/DeltaText）
    await expect(section.locator(".datarow", { hasText: "船舶" })).toBeVisible({ timeout: 15000 });
    await expect(section.locator(".datarow", { hasText: "船舶" }).locator(".delta")).toHaveText("+4.46%");
    // 右列资金流净额（净流入/净流出）
    await expect(section.getByText(/净流入|净流出/).first()).toBeVisible({ timeout: 15000 });
  });

  test("板块区两列独立降级：sectors 挂掉不牵连资金流列", async ({ page }) => {
    // 后注册优先：覆盖 beforeEach 的 sectors mock，资金流仍返回
    await page.route("**/api/v1/market/sectors", (route) => route.abort());
    await page.goto("/#sectors");
    const section = page.getByTestId("section-sectors");
    await expect(section).toBeVisible();
    await expect(section.getByText("行业涨跌暂不可用")).toBeVisible({ timeout: 15000 });
    // 右列独立存活
    await expect(section.getByText(/净流入|净流出/).first()).toBeVisible({ timeout: 15000 });
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
