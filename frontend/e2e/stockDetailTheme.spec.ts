import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * 个股详情「估值」tab 的暗色主题跟随（P1-4 / F8）。
 *
 * 缺陷背景：`ValuationTab` / `FinancialTab` 曾直接 `import ReactECharts from "echarts-for-react"`，
 * 绕过 `shared/ui/EChart.tsx` 的 `withChartTheme`。ECharts 不消费 CSS var，暗色卡片底
 * （#1e222d）上仍是 ECharts 默认的深灰轴标签/图例 —— 人眼才看得出的可读性缺陷，
 * 编译与运行时都不报错。所以这里钉住三件事：
 *   1. 暗色标记确实挂上（`html[data-theme="dark"]`，与 `darkmode.spec.ts` 同一机制）；
 *   2. 估值走势**真的渲染出了 canvas**（图表没被主题改造弄挂/弄空）；
 *   3. 控制台没有含 `echarts` 的报错（canvas 渲染期异常的兜底信号）。
 *
 * **刻意不断言像素颜色**：取色断言会把「主题色板微调」变成 e2e 红灯，而真正要守的是
 * 「图走封装 + 能渲染」；颜色契约由 `EChart.tsx` + `check-design-tokens.mjs` 守。
 *
 * 数据全部 mock（同 `darkmode.spec.ts` 的口径）：本用例只验主题/渲染，不该因后端
 * 网关抖动或某只票当天没有估值样本而红。先登记 `/api/v1/**` 兜底空集合，再用后登记的
 * 具体路由覆盖——Playwright 的**后登记优先**保证了覆盖顺序。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

/** 与 `darkmode.spec.ts` 同款：初始化前写 localStorage，避免首帧闪色。 */
const FORCE_DARK = () => localStorage.setItem("stockbot-theme", "dark");

/** 个股页最小可渲染载荷（与 `conceptBoard.spec.ts` 的个股用例同一形状）。 */
const STOCK_600519 = {
  symbol: "600519",
  name: "贵州茅台",
  exchange: "Shanghai_Stocks",
  category: "stock",
  asof: "2026-10-16T00:00:00Z",
  latest_price: 1500.0,
  change_percent: 1.2,
  latest_quote_date: "2026-10-16",
};

/** 估值历史：3 个交易日的折线（够 ECharts 建 canvas + 画一条线）。 */
const VALUATION_HISTORY = {
  exchange: "Shanghai_Stocks",
  symbol: "600519",
  metric: "pe_ttm",
  range: "3y",
  as_of: "2026-10-16",
  current: 20.4,
  percentiles: { "1y": 83.4, "3y": 69.8, "5y": 69.8 },
  stats: { min: 17.66, median: 20.08, max: 23.64, sample_count: 3 },
  series: [
    { trade_date: "2026-10-14", value: 20.1 },
    { trade_date: "2026-10-15", value: 20.3 },
    { trade_date: "2026-10-16", value: 20.4 },
  ],
};

async function mockStockDetailApis(page: Page) {
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        id: "u-dark-chart",
        username: "dark_chart",
        email: "dark-chart@example.com",
        display_name: "Dark 图表用户",
        status: "active",
        roles: ["trader"],
        permissions: [],
      }),
    }),
  );
  // 兜底：其余接口一律空集合（本用例只关心图表，不关心它们的具体数据）
  await page.route("**/api/v1/**", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "[]" }),
  );
  // 后登记 ⇒ 优先命中（个股本体与估值历史）
  await page.route("**/stocks/*/enriched", (route) => route.fulfill({ json: STOCK_600519 }));
  await page.route("**/valuation-history*", (route) =>
    route.fulfill({ json: VALUATION_HISTORY }),
  );
}

test.describe("个股详情 · 估值 tab 暗色主题", () => {
  test.use({ colorScheme: "light" });

  test("/stock/600519?tab=valuation 暗色下估值图渲染出 canvas，且无 echarts 控制台报错", async ({
    page,
  }) => {
    const echartsErrors: string[] = [];
    page.on("console", (msg) => {
      if (msg.type() === "error" && /echarts/i.test(msg.text())) echartsErrors.push(msg.text());
    });
    page.on("pageerror", (err) => {
      if (/echarts/i.test(err.message)) echartsErrors.push(err.message);
    });

    await mockStockDetailApis(page);
    await page.addInitScript(FORCE_DARK);

    await page.goto("/stock/600519?tab=valuation");

    // 1. 暗色标记：主题真的落在 <html> 上（否则本用例测的还是浅色）
    await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

    // 2. 估值 tab 就位。
    //    注：`?tab=` 深链由并行的 P1-3（Tab 进 URL）落地；本用例在其落地前后都要绿，
    //    故先按深链打开，再**幂等**确保 tab 被选中（已选中则不动）。
    const valuationTab = page.getByRole("tab", { name: "估值", exact: true });
    await expect(valuationTab).toBeVisible();
    if ((await valuationTab.getAttribute("aria-selected")) !== "true") {
      await valuationTab.click();
    }

    // 3. 图表卡片与 canvas：card 是锚点（图表只在有数据时渲染），canvas 证明 ECharts 真的 init 了
    const chartCard = page.locator(".ant-card").filter({ hasText: "估值历史走势" });
    await expect(chartCard).toBeVisible();
    const canvas = chartCard.locator("canvas").first();
    await expect(canvas).toBeVisible();
    const box = await canvas.boundingBox();
    expect(box, "估值图 canvas 应有非零尺寸（主题注入不应把容器画塌）").not.toBeNull();
    expect(box!.width).toBeGreaterThan(0);
    expect(box!.height).toBeGreaterThan(0);

    // 4. 控制台无 echarts 相关报错
    expect(echartsErrors, `控制台出现 echarts 报错：\n${echartsErrors.join("\n")}`).toEqual([]);
  });
});
