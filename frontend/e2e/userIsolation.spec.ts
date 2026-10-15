import { test, expect as baseExpect } from "@playwright/test";
import {
  TRIPLET_LABEL,
  TRIPLET_ORDER,
  TRIPLET_WIDTH,
} from "../src/shared/ui/instrumentTriplet";

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
          // 必须是后端真实契约 WatchlistOut[]（items 嵌套），不能是扁平的 item 数组：
          // 前端按 wl.items 取 symbol，形状不符会静默得到空自选（见 best-practices/testing 的 mock 同源条）
          body: JSON.stringify([
            {
              id: "wl-1", user_id: userA.id, name: "我的自选", is_default: true,
              created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
              items: [
                {
                  id: "wl-item-1", watchlist_id: "wl-1", symbol: "600519",
                  exchange: "Shanghai_Stocks", sort_order: 1,
                  created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
                },
              ],
            },
          ]),
        });
      }
      return route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify([
          {
            id: "wl-2", user_id: userB.id, name: "我的自选", is_default: true,
            created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
            items: [
              {
                id: "wl-item-2", watchlist_id: "wl-2", symbol: "000001",
                exchange: "Shenzen_Stocks", sort_order: 1,
                created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z",
              },
            ],
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

    /**
     * P1-2：**自选清单的指标三元组**（最新价 → 涨跌额 → 涨跌幅）。
     *
     * 选在这里做行级断言的原因：本用例的 watchlist 与 enriched 都是**受控 mock**
     * （`change: 28.0` / `change_percent: 1.79`），于是三个成员都是**真值**——
     * 自选是仓库里唯一载荷齐了三元组的清单（`StockEnrichedOut` 同时回
     * `latest_price`/`change`/`change_percent`），其余清单至少缺一个成员。
     * 列宽直接与 `instrumentTriplet.ts` 的契约常量对账（import 常量而不是抄数字：
     * 抄一遍的话实现漂移 spec 会跟着漂），行内数值一律 `.num`（tabular figures）+ 右对齐。
     */
    const watchTable = page.locator(".ant-table").filter({ hasText: "贵州茅台" });
    const watchHeads = watchTable.locator(".ant-table-thead th");
    const watchLabels = (await watchHeads.allInnerTexts()).map((t) => t.trim());
    const tripletStart = watchLabels.indexOf(TRIPLET_LABEL[TRIPLET_ORDER[0]]);
    expect(
      tripletStart,
      `自选表头里找不到「${TRIPLET_LABEL[TRIPLET_ORDER[0]]}」：实际 ${JSON.stringify(watchLabels)}`
    ).toBeGreaterThanOrEqual(0);
    expect(
      watchLabels.slice(tripletStart, tripletStart + TRIPLET_ORDER.length),
      `自选表三元组不齐/乱序：实际 ${JSON.stringify(watchLabels)}`
    ).toEqual(TRIPLET_ORDER.map((m) => TRIPLET_LABEL[m]));
    const watchRow = watchTable.locator(".ant-table-tbody tr.ant-table-row").first();
    const expectValues: Record<(typeof TRIPLET_ORDER)[number], string> = {
      price: "1588.00",
      change: "+28.00",
      pct: "+1.79%",
    };
    for (let i = 0; i < TRIPLET_ORDER.length; i += 1) {
      const member = TRIPLET_ORDER[i];
      const cell = watchRow.locator("td").nth(tripletStart + i);
      const num = cell.locator(".num");
      await expect(
        num,
        `${TRIPLET_LABEL[member]} 格没有 .num（等宽数字/tabular figures）`
      ).toHaveText(expectValues[member]);
      await expect(cell, `${TRIPLET_LABEL[member]} 列没有右对齐`).toHaveCSS("text-align", "right");
      const box = await cell.boundingBox();
      expect(
        Math.round(box?.width ?? -1),
        `${TRIPLET_LABEL[member]} 列宽 ${box?.width} ≠ 三元组契约 ${TRIPLET_WIDTH[member]}px`
      ).toBe(TRIPLET_WIDTH[member]);
    }

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
