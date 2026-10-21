import { expect, test, type Page } from "@playwright/test";

/**
 * 自选「加了不显示 / 不能删」回归（2026-10-19）。
 *
 * 三条都来自真实事故，且都能在**不接后端**的前提下复现：
 *  1. 未登录（或登录态还没确定）时加的自选只写 localStorage，登录后从不合并到账户
 *     ⇒ `/me` 永远看不到，刷新也没用；
 *  2. 某只票的 enrichment 取不到时，旧实现 `Promise.all().filter(Boolean)` 把它**静默删行**
 *     ⇒ 标题显示 N、表里只有 N-1 行，且丢掉的那行没有删除按钮（"不支持删除"）；
 *  3. 删除按钮是纯图标且没有 accessible name ⇒ 屏幕阅读器/自动化都定位不到。
 *
 * 三个读面都 mock（`/auth/session`、`/api/v1/watchlists*`、`/api/v1/exchanges/…/enriched`），
 * 因为 dev server 的 `/api` 代理指向本机 gateway，e2e 不依赖它。
 */

const USER = {
  id: "usr-wl-e2e",
  username: "wl_reader",
  email: "wl@example.com",
  display_name: "自选读者",
  status: "active",
  is_superuser: false,
  roles: ["trader"],
  permissions: ["stocks:read", "watchlists:write"],
  created_at: "2026-10-01T00:00:00Z",
};

function enriched(symbol: string, name: string) {
  const sh = symbol.startsWith("6");
  return {
    exchange: sh ? "Shanghai_Stocks" : "Shenzen_Stocks",
    symbol,
    name,
    category: "主板",
    csrc_desc: "测试行业",
    latest_price: 10.5,
    prev_close: 10,
    open: 10.1,
    high: 10.6,
    low: 9.9,
    change: 0.5,
    change_percent: 5.0,
    volume: 1000,
    amount: 10000,
    total_mv: 1000,
    circ_mv: 900,
    asof: "2026-10-19",
  };
}

interface MockOptions {
  /** 初始自选（symbol 列表） */
  items: string[];
  /** 这些 symbol 的 enrichment 全部 404（模拟"行情未取到"） */
  enrichedFail?: string[];
}

interface MockHandle {
  posts: { symbol: string }[];
  deletes: string[];
  items: string[];
}

/** 有状态的 watchlist mock：POST/DELETE 会改变后续 GET 的结果（与真后端同语义）。 */
async function mockWatchlistStack(page: Page, opts: MockOptions): Promise<MockHandle> {
  const handle: MockHandle = { posts: [], deletes: [], items: [...opts.items] };
  const fail = new Set(opts.enrichedFail ?? []);

  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(USER),
    })
  );

  // `/me` 的 tab 角标会读 triage：这里不需要它，直接 404（角标不显示数字）
  await page.route("**/api/v1/watchlists/triage**", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: "{}" })
  );

  // 先注册 DELETE（更具体的路径），再注册 POST/GET
  await page.route("**/api/v1/watchlists/items/*", async (route) => {
    const req = route.request();
    if (req.method() !== "DELETE") return route.fallback();
    const symbol = decodeURIComponent(new URL(req.url()).pathname.split("/").pop() ?? "");
    handle.deletes.push(symbol);
    handle.items = handle.items.filter((s) => s !== symbol);
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ deleted: true, symbol }),
    });
  });

  await page.route("**/api/v1/watchlists/items", async (route) => {
    const req = route.request();
    if (req.method() !== "POST") return route.fallback();
    const body = JSON.parse(req.postData() ?? "{}") as { symbol?: string };
    const symbol = String(body.symbol ?? "");
    handle.posts.push({ symbol });
    if (!handle.items.includes(symbol)) handle.items.push(symbol);
    return route.fulfill({
      status: 201,
      contentType: "application/json",
      body: JSON.stringify({
        id: `item-${symbol}`,
        watchlist_id: "wl-1",
        symbol,
        exchange: null,
        sort_order: handle.items.length - 1,
        notes: null,
        created_at: "2026-10-19T00:00:00Z",
        updated_at: "2026-10-19T00:00:00Z",
      }),
    });
  });

  await page.route("**/api/v1/watchlists", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify([
        {
          id: "wl-1",
          user_id: USER.id,
          name: "默认自选",
          is_default: true,
          created_at: "2026-10-01T00:00:00Z",
          updated_at: "2026-10-01T00:00:00Z",
          items: handle.items.map((symbol, idx) => ({
            id: `item-${symbol}`,
            watchlist_id: "wl-1",
            symbol,
            exchange: null,
            sort_order: idx,
            notes: null,
            created_at: "2026-10-19T00:00:00Z",
            updated_at: "2026-10-19T00:00:00Z",
          })),
        },
      ]),
    })
  );

  await page.route("**/api/v1/exchanges/*/stocks/*/enriched", (route) => {
    const path = new URL(route.request().url()).pathname;
    const parts = path.split("/");
    const symbol = parts[parts.length - 2];
    if (fail.has(symbol)) {
      return route.fulfill({
        status: 404,
        contentType: "application/json",
        body: JSON.stringify({ detail: "Stock not found" }),
      });
    }
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(enriched(symbol, symbol === "600519" ? "贵州茅台" : "平安银行")),
    });
  });

  return handle;
}

test("未登录时的本地自选，登录后必须合并到账户（不是永远留在 localStorage）", async ({
  page,
}) => {
  // 模拟"没登录时加了 000001"：zustand persist 的存储格式
  await page.addInitScript(() => {
    window.localStorage.setItem(
      "stock-bot-watchlist",
      JSON.stringify({ state: { items: ["000001"] }, version: 0 })
    );
  });
  const handle = await mockWatchlistStack(page, { items: [] });

  await page.goto("/me?tab=watchlist");

  // 合并发生：POST 一次 000001
  await expect.poll(() => handle.posts.length).toBe(1);
  expect(handle.posts[0]).toEqual({ symbol: "000001" });
  await expect(page.getByText("已把 1 只本地自选同步到账户")).toBeVisible({ timeout: 10_000 });

  // 账户侧能看到，本地那份已清空（不再当孤儿）
  await expect(page.getByText("我的自选（1）")).toBeVisible();
  const stored = await page.evaluate(() => window.localStorage.getItem("stock-bot-watchlist"));
  expect(JSON.parse(stored ?? "{}").state.items).toEqual([]);
});

test("行情取不到的标的必须留行：显示占位、且仍可删除", async ({ page }) => {
  const handle = await mockWatchlistStack(page, {
    items: ["600519", "000001"],
    enrichedFail: ["000001"],
  });

  await page.goto("/me?tab=watchlist");

  // 两行都在（旧实现会静默丢掉取不到的那一行）
  await expect(page.getByText("我的自选（2）")).toBeVisible();
  await expect(page.locator(".ant-table-tbody tr.ant-table-row")).toHaveCount(2);
  await expect(page.getByTestId("watchlist-unresolved")).toContainText("000001");
  await expect(page.getByTestId("watchlist-unresolved-hint")).toContainText("有 1 只自选的行情暂未取到");

  // 取不到的那一行**仍有删除入口**，且按钮有可访问名称
  const removeBtn = page.getByRole("button", { name: "移除自选 000001" });
  await expect(removeBtn).toBeVisible();
  await removeBtn.click();

  await expect.poll(() => handle.deletes).toEqual(["000001"]);
  await expect(page.locator(".ant-table-tbody tr.ant-table-row")).toHaveCount(1);
  await expect(page.getByText("已从自选移除 000001")).toBeVisible();
});

test("正常行也能删：删除按钮不再是无名图标", async ({ page }) => {
  const handle = await mockWatchlistStack(page, { items: ["600519"] });

  await page.goto("/me?tab=watchlist");
  await expect(page.getByText("贵州茅台")).toBeVisible();

  await page.getByRole("button", { name: "移除自选 600519" }).click();

  await expect.poll(() => handle.deletes).toEqual(["600519"]);
  await expect(page.locator(".ant-table-tbody tr.ant-table-row")).toHaveCount(0);
});

test("排序一次只出现在一列上：列 key 必须唯一（不是六列共用 dataIndex）", async ({ page }) => {
  // 回归：把行类型改成 `{symbol, stock}` 后，所有列都写 `dataIndex: "symbol"`，
  // antd 用 `key ?? dataIndex` 当列标识 ⇒ 六列共用一个列 key，`useSorter` 的排序状态
  // 也共用：点「最新价」会让最新价/涨跌额/涨跌幅/总市值四列同时出现升序箭头。
  await mockWatchlistStack(page, { items: ["600519", "000001"] });
  await page.goto("/me?tab=watchlist");
  await expect(page.locator(".ant-table-tbody tr.ant-table-row")).toHaveCount(2);

  const sortedHeaders = async () =>
    page
      .locator(".ant-table-thead th[aria-sort]")
      .evaluateAll((els) => els.map((el) => `${el.innerText.trim()}=${el.getAttribute("aria-sort")}`));

  await page.getByRole("columnheader", { name: /最新价/ }).click();
  expect(await sortedHeaders()).toEqual(["最新价=ascending"]);

  await page.getByRole("columnheader", { name: /涨跌幅/ }).click();
  expect(await sortedHeaders()).toEqual(["涨跌幅=ascending"]);
});
