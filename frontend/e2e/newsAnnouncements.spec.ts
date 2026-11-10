import { expect, test } from "@playwright/test";

/**
 * `/news` 资讯页 · 公告 Tab 接真实读面（Wave 2 Plan 1）
 *
 * ## 为什么整页只 mock 一个读面
 *
 * `/news` 是**匿名可达**页（无 `RequireAuth`），页面自己只消费 `GET /api/v1/content`。
 * 但真实公告是"按交易日推进"的数据：不钉住它，`content-row` 是否出现取决于
 * 本机库今天恰好有没有抓过公告——那是"数据运气"，不是契约。故把列表读面钉成
 * 一条固定公告，用例锁的是**结构与可溯源**，不锁真实公告内容。
 *
 * ## 断言的取舍
 *
 * - 公告 Tab 必须渲染出 `content-row`（真组件，不是空壳）；
 * - 每行的标题就是**原文入口**：`href` 必须是 `https://`（后端只存 URL、不落正文，
 *   "可溯源"这件事在 UI 上就只有这一个出口）；
 * - 另外三类（政策 / 研报 / 快讯）**尚未接入**：必须给显式空态，且**不出现**
 *   `规划中` 这类整模块占位文案——否则用户会以为资讯模块整体没做完。
 */

const ITEM = {
  id: 1,
  content_type: "announcement",
  source_name: "cninfo",
  publisher_tier: "legal",
  publisher: "西麦食品",
  title: "2026年第三季度报告",
  published_at: "2026-10-30T18:00:00+08:00",
  ingested_at: "2026-10-30T18:05:00+08:00",
  url: "https://example.com/a.pdf",
  extracted: { subtype: "report", sec_name: "西麦食品", sec_code: "002956" },
  review_status: "auto",
  user_state: null,
  entities: [{ entity_type: "stock", entity_id: "002956" }],
  metrics: [
    {
      metric_key: "revenue",
      period: "2026Q3",
      value: 123000000,
      value_text: null,
      unit: "CNY",
      source_span: { kind: "structured" },
    },
  ],
};

test.describe("/news 资讯", () => {
  test.beforeEach(async ({ page }) => {
    await page.route("**/api/v1/content*", (route) =>
      route.fulfill({ status: 200, json: [ITEM] })
    );
  });

  test("默认落在公告 Tab：真实列表 + 每行都能回到原文", async ({ page }) => {
    await page.goto("/news", { waitUntil: "domcontentloaded" });

    // 页头承诺了"带状态、可溯源"，页面上就得有这句（不是只写在代码注释里）
    await expect(page.getByText("资讯 · 带状态、可溯源的内容流")).toBeVisible();

    const announcementsTab = page.getByRole("tab", { name: "公告" });
    await expect(announcementsTab).toBeVisible();
    await expect(announcementsTab).toHaveAttribute("aria-selected", "true");

    // 真组件渲染：有行、有标题、标题即原文链接
    const row = page.getByTestId("content-row").first();
    await expect(row).toBeVisible();
    await expect(row).toContainText("2026年第三季度报告");
    await expect(row).toContainText("西麦食品");

    const link = row.getByTestId("content-original-link");
    await expect(link).toHaveAttribute("href", /^https:\/\//);
    // 新窗口打开 = 不把用户从工作台里带走
    await expect(link).toHaveAttribute("target", "_blank");

    // 整页不得回落到「规划中」占位文案
    await expect(page.getByText("规划中")).toHaveCount(0);
  });

  test("其余三类显式空态，且不伪造数据", async ({ page }) => {
    for (const [tab, label] of [
      ["policy", "政策"],
      ["research", "研报"],
      ["flash", "快讯"],
    ] as const) {
      await page.goto(`/news?tab=${tab}`, { waitUntil: "domcontentloaded" });
      await expect(page.getByRole("tab", { name: label })).toHaveAttribute("aria-selected", "true");
      await expect(page.getByText("该类别尚未接入（Wave 2 Plan 2–4）")).toBeVisible();
      // 没接入 ≠ 没有内容：不许渲染列表行
      await expect(page.getByTestId("content-row")).toHaveCount(0);
      await expect(page.getByText("规划中")).toHaveCount(0);
    }
  });
});
