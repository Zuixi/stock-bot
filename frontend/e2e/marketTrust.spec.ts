import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, test, type Page } from "@playwright/test";

/**
 * Task 15：热门板块的「可下钻 + 可证伪」信任面。
 *
 * 覆盖三件事：
 * 1. 板块条目 → 成分股抽屉（东财 `GET /market/boards/{code}/stocks`）；
 * 2. 列表页排序 / 搜索 / `?board=` 高亮 / 切分类保留高亮；
 * 3. 产地降级（东财不可用 → 本地分组）必须如实上屏，不得把回落数据冒充成东财板块。
 *
 * 全部走 `page.route` mock：活栈板块数据是 T+1 快照，条数/成分股随交易日漂移；
 * mock 载荷是**实抓快照**（`e2e/fixtures/hotBoards*.sample.json`），只保证形状与后端同键集，
 * 避免手写形状与真实契约漂移（best-practices）。
 */

const FIXTURES = join(dirname(fileURLToPath(import.meta.url)), "fixtures");
const loadFixture = (name: string) => JSON.parse(readFileSync(join(FIXTURES, name), "utf8"));

const INDUSTRY = loadFixture("hotBoardsIndustry.sample.json");
const CONCEPT = loadFixture("hotBoardsConcept.sample.json");
const BOARD_STOCKS = loadFixture("boardStocks.sample.json");

const expect15s = expect.configure({ timeout: 15_000 });

/** 按 category 分派 hot-boards mock；未覆盖的分类返回空列表（不泄漏活栈数据）。 */
async function mockHotBoards(page: Page, byCategory: Record<string, unknown>) {
  await page.route("**/api/v1/market/hot-boards*", (route) => {
    const category = new URL(route.request().url()).searchParams.get("category") ?? "industry";
    const payload = byCategory[category] ?? {
      as_of: null,
      as_of_quality: "partial",
      as_of_reason: null,
      source: "eastmoney_boards",
      degraded_reason: null,
      items: [],
    };
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(payload),
    });
  });
}

async function mockBoardStocks(page: Page, payload: unknown, status = 200) {
  await page.route("**/api/v1/market/boards/*/stocks*", (route) =>
    route.fulfill({
      status,
      contentType: "application/json",
      body:
        status === 200
          ? JSON.stringify(payload)
          : JSON.stringify({ detail: "eastmoney board constituents unavailable" }),
    })
  );
}

/** 板块行的「名称」列（第一列 strong 文本；不掺 code，避免断言被第二行污染）。 */
function boardNames(page: Page) {
  return page.locator(".ant-table tbody tr td:first-child strong");
}

/** 数据行（排除 antd `scroll.x` 额外渲染的 `.ant-table-measure-row`，它不是数据）。 */
function dataRows(page: Page) {
  return page.locator(".ant-table tbody tr:not(.ant-table-measure-row)");
}

test.describe("热门板块信任面（Task 15）", () => {
  test("卡片点击板块条目打开成分股抽屉（含板块名 + code）", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY });
    await mockBoardStocks(page, BOARD_STOCKS);

    await page.goto("/market");
    await page.getByRole("tab", { name: "A股全景" }).click();
    const card = page.locator(".ant-card").filter({ hasText: "A股热门板块" });
    await expect15s(card).toBeVisible();

    // 东财来源不得出现降级标注（否则「降级标注」就成了摆设）
    await expect(card.getByText(/本地分组/)).toHaveCount(0);

    await card.getByText("种子", { exact: true }).click();
    const drawer = page.getByRole("dialog");
    await expect15s(drawer).toBeVisible();
    // API 不回显板块身份，抽屉必须自己说清楚「这是哪个板块」
    await expect15s(drawer.getByText("种子", { exact: true })).toBeVisible();
    await expect15s(drawer.getByText("BK1518")).toBeVisible();
    // 成分股真渲染出来（不是空表）
    await expect15s(drawer.getByText("敦煌种业")).toBeVisible();
    await expect(drawer.locator("tbody tr")).toHaveCount(BOARD_STOCKS.length);
  });

  test("成分股上游 502 → 抽屉显示错误态而非空表", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY });
    await mockBoardStocks(page, null, 502);

    await page.goto("/market/hot-sectors/industry");
    await expect15s(boardNames(page).first()).toHaveText("种子");
    await page.locator("tbody tr").filter({ hasText: "BK1518" }).first().click();
    const drawer = page.getByRole("dialog");
    await expect15s(drawer).toBeVisible();
    await expect15s(drawer.getByText(/不可用|加载失败/).first()).toBeVisible();
    // 关键：不得渲染 0 行空表（会谎报「该板块没有成分股」）
    await expect(drawer.locator("tbody tr")).toHaveCount(0);
  });

  test("成分股成功但为空 → 空态而非错误态", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY });
    await mockBoardStocks(page, []);

    await page.goto("/market/hot-sectors/industry");
    await expect15s(boardNames(page).first()).toHaveText("种子");
    await page.locator("tbody tr").filter({ hasText: "BK1518" }).first().click();
    const drawer = page.getByRole("dialog");
    await expect15s(drawer).toBeVisible();
    // 空数组 = 板块真的没有成分股：空态，且不得出现错误文案
    await expect15s(drawer.getByText("该板块暂无成分股数据")).toBeVisible();
    await expect(drawer.getByText(/不可用|加载失败/)).toHaveCount(0);
  });

  test("列表页按成交额 / 主力净额排序，顺序确实变化", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY });
    await page.goto("/market/hot-sectors/industry");
    await expect15s(boardNames(page).first()).toHaveText("种子"); // 默认涨跌幅降序

    const byAmountDesc = [...INDUSTRY.items]
      .sort((a, b) => (b.amount ?? 0) - (a.amount ?? 0))
      .map((i) => i.name);
    const byInflowDesc = [...INDUSTRY.items]
      .sort((a, b) => (b.mainNetInflow ?? 0) - (a.mainNetInflow ?? 0))
      .map((i) => i.name);

    // antd 首次点击升序、第二次降序 → 点两下取降序
    await page.getByRole("columnheader").filter({ hasText: "成交额" }).click();
    await page.getByRole("columnheader").filter({ hasText: "成交额" }).click();
    await expect.poll(() => boardNames(page).allTextContents()).toEqual(byAmountDesc);

    await page.getByRole("columnheader").filter({ hasText: "主力净额" }).click();
    await page.getByRole("columnheader").filter({ hasText: "主力净额" }).click();
    await expect.poll(() => boardNames(page).allTextContents()).toEqual(byInflowDesc);

    // 两个维度的排序结果必须不同，否则「按维度排序」是假象
    expect(byAmountDesc).not.toEqual(byInflowDesc);
  });

  test("搜索框按名称 / code 过滤列表", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY });
    await page.goto("/market/hot-sectors/industry");

    const search = page.getByPlaceholder(/搜索板块/);
    await expect15s(search).toBeVisible();

    await search.fill("养殖");
    await expect15s(dataRows(page)).toHaveCount(1);
    await expect15s(dataRows(page).first()).toContainText("其他养殖");

    await search.fill("BK1261");
    await expect15s(dataRows(page)).toHaveCount(1);
    await expect15s(dataRows(page).first()).toContainText("种植业");

    await search.fill("");
    await expect15s(dataRows(page)).toHaveCount(INDUSTRY.items.length);
  });

  test("?board= 深链高亮，切分类保留参数、切回恢复高亮", async ({ page }) => {
    await mockHotBoards(page, { industry: INDUSTRY, concept: CONCEPT });
    await page.goto("/market/hot-sectors/industry?board=BK1518");

    const selected = page.locator("tbody tr.ant-table-row-selected");
    await expect15s(selected).toHaveCount(1);
    await expect15s(selected).toContainText("BK1518");

    // 切到概念：BK1518 不在概念列表 → 不高亮（"where meaningful"），但参数必须保留
    await page.locator(".ant-segmented-item").filter({ hasText: "概念板块" }).click();
    await expect(page).toHaveURL(/\/market\/hot-sectors\/concept\?board=BK1518/);
    await expect15s(page.locator("tbody tr.ant-table-row-selected")).toHaveCount(0);

    // 切回行业：高亮恢复（参数没丢）
    await page.locator(".ant-segmented-item").filter({ hasText: "行业板块" }).click();
    await expect(page).toHaveURL(/\/market\/hot-sectors\/industry\?board=BK1518/);
    await expect15s(page.locator("tbody tr.ant-table-row-selected")).toHaveCount(1);
    await expect15s(page.locator("tbody tr.ant-table-row-selected")).toContainText("BK1518");
  });

  test("东财不可用回落本地分组 → 卡片如实标注降级", async ({ page }) => {
    const fallback = {
      ...INDUSTRY,
      source: "local_grouping",
      degraded_reason: "eastmoney_unavailable",
      items: INDUSTRY.items.map((item: Record<string, unknown>) => ({
        ...item,
        code: "",
        leaders: [],
      })),
    };
    await mockHotBoards(page, { industry: fallback });

    await page.goto("/market");
    await page.getByRole("tab", { name: "A股全景" }).click();
    const card = page.locator(".ant-card").filter({ hasText: "A股热门板块" });
    await expect15s(card.getByText(/本地分组/).first()).toBeVisible();
    await expect15s(card.getByText(/东财板块不可用/).first()).toBeVisible();

    // 列表页同样不得吞掉产地
    await page.goto("/market/hot-sectors/industry");
    await expect15s(page.getByText(/本地分组/).first()).toBeVisible();
  });
});
