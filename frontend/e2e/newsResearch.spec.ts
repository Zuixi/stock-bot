import { expect, test } from "@playwright/test";

/**
 * `/news` 资讯页 · 研报 Tab（Wave 2 Plan 2）
 *
 * 锁住三件会被静默做错的事：
 *  1. 默认只显示自动判定**有效**的研报（`include_ineffective` 不传）；
 *  2. 打开「显示被过滤」后请求带 `include_ineffective=true`，被过滤行带原因；
 *  3. `core_view` 未抽取时必须明确说「未抽取」，不能编造观点，也不能显示 `0`。
 *
 * 页面匿名可达，只消费 `GET /api/v1/content`；mock 按 query 参数返回 1 或 2 行。
 */

const BASE = {
  content_type: "report",
  source_name: "eastmoney_report",
  publisher_tier: "sellside",
  publisher: "华泰证券",
  ingested_at: "2026-11-11T10:00:00+08:00",
  review_status: "new",
  user_state: null,
  entities: [
    { entity_type: "stock", entity_id: "002714" },
    { entity_type: "industry", entity_id: "养殖业" },
  ],
};

const GOOD = {
  ...BASE,
  id: 1,
  title: "牧原股份(002714)三季报点评：成本领先，出栏稳增",
  published_at: "2026-11-11T08:00:00+08:00",
  url: "https://pdf.dfcfw.com/pdf/H3_good_1.pdf",
  effective: true,
  ineffective_reasons: [],
  extracted: { core_view_status: "not_extracted", industry: "养殖业", rating_change: 1 },
  metrics: [
    { metric_key: "rating", period: null, value: null, value_text: "买入", unit: null, source_span: {} },
    { metric_key: "rating_change", period: null, value: 1, value_text: null, unit: null, source_span: {} },
    { metric_key: "target_price", period: null, value: 55.5, value_text: null, unit: "CNY", source_span: {} },
    { metric_key: "eps_forecast", period: "2026E", value: 2.1, value_text: null, unit: "CNY", source_span: {} },
    { metric_key: "eps_forecast", period: "2027E", value: 2.6, value_text: null, unit: "CNY", source_span: {} },
    { metric_key: "pe_forecast", period: "2026E", value: 20.1, value_text: null, unit: "x", source_span: {} },
  ],
};

const FILTERED = {
  ...BASE,
  id: 2,
  title: "某公司(000001)点评：维持评级",
  published_at: "2026-11-10T08:00:00+08:00",
  url: "https://pdf.dfcfw.com/pdf/H3_filtered_1.pdf",
  effective: false,
  ineffective_reasons: ["superseded"],
  extracted: { core_view_status: "not_extracted", industry: "银行", rating_change: 1, superseded_by: 1 },
  metrics: [
    { metric_key: "rating", period: null, value: null, value_text: "增持", unit: null, source_span: {} },
  ],
};

test.describe("/news 研报 Tab", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/v1/content*", (route) => {
      const include = new URL(route.request().url()).searchParams.get("include_ineffective");
      return route.fulfill({ status: 200, json: include === "true" ? [GOOD, FILTERED] : [GOOD] });
    });
  });

  test("默认只显示有效研报，结构化字段与未抽取的核心观点都上屏", async ({ page }) => {
    await page.goto("/news?tab=research", { waitUntil: "domcontentloaded" });

    await expect(page.getByRole("tab", { name: "研报" })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    const rows = page.getByTestId("content-row");
    await expect(rows).toHaveCount(1);

    const row = rows.first();
    await expect(row).toContainText("买入");
    await expect(row).toContainText("55.50");
    await expect(row).toContainText("2026E");
    await expect(row).toContainText("核心观点未抽取");
    await expect(row.getByTestId("content-original-link")).toHaveAttribute("href", /^https:\/\//);
    await expect(page.getByTestId("research-ineffective-reasons")).toHaveCount(0);
  });

  test("打开「显示被过滤」后出现被过滤行与原因", async ({ page }) => {
    await page.goto("/news?tab=research", { waitUntil: "domcontentloaded" });
    await expect(page.getByTestId("content-row")).toHaveCount(1);

    await page.getByTestId("research-include-ineffective").click();

    await expect(page.getByTestId("content-row")).toHaveCount(2);
    const reasons = page.getByTestId("research-ineffective-reasons");
    await expect(reasons).toBeVisible();
    await expect(reasons).toContainText("重复");
  });
});
