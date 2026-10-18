import { test, expect as baseExpect, type Page, type Route } from "@playwright/test";

/**
 * P0-3 E2E：个股详情「相关数据」的两条硬约束 + 三个回归护栏。
 *
 * 1. **失败 ≠ 空**（F3）：五个 tab 过去统一 `dataSource={q.data ?? []} + loading={q.isLoading}`，
 *    500/断网时 `data` 仍是 `undefined`，表格直接落进 `locale.emptyText`，页面于是宣称
 *    「暂无上榜记录」——一次失败被伪装成一条事实。断言：mock 500 时**错误块 + 重试**可见，
 *    且该 tab 的空态文案**不可见**；恢复成 200 后点重试，错误块消失。
 * 2. **龙虎榜按标的上送**（P0-2 前端半）：`fetchDragonTiger(limit, symbol?)` 必须把 symbol 送出去；
 *    客户端 `.filter()` 只是护栏，不再是唯一过滤手段。
 * 3. **护栏不能自己说谎**：接口有返回但筛不出本股（= 后端没按 symbol 过滤，今天的 :80 就是这样）
 *    必须显示**独立文案的错误态**，不得落成「暂无上榜记录」。
 * 4. **成功且为空仍是空态**：200 + `[]` 显示「暂无…」，不得升级成错误 UI。
 * 5. **另一个调用点不受影响**：数据面的 `fetchDragonTiger(15)` 请求 URL **不带** `symbol`。
 *
 * 断言纪律：
 * - `page.on("request")` / `page.route(...)` 必须在 `goto` 或触发查询（点 tab）**之前**注册，
 *   否则首个请求漏记 / 打到真后端；
 * - mock 每个用例只落在**一个**端点上，不碰其余端点（它们的真失败与本用例结论无关）；
 * - 不断言行数：运行中的后端可能还没部署 `symbol` 参数，所以「URL 带 symbol」才是判别性证据。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const DRAGON_TIGER_GLOB = "**/dragon-tiger**";

const fulfillJson = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

const fail500 = (route: Route) => route.fulfill({ status: 500, body: "boom" });

/** 相关数据卡的作用域：避免与市场页的「龙虎榜」tab、脚注文案撞 strict mode。 */
function relatedEventsCard(page: Page) {
  return page.locator(".ant-card").filter({ hasText: "相关数据" });
}

/** 打开详情页并等到「相关数据」卡就绪；个股缺失时跳过（与 marketDataFace.spec.ts 同策略）。 */
async function openRelatedEvents(page: Page) {
  await page.goto("/stock/600519");
  const card = relatedEventsCard(page);
  const notFound = page.getByText("未找到该股票");
  await expect(notFound.or(card).first()).toBeVisible();
  test.skip(await notFound.isVisible(), "个股 600519 数据缺失，跳过");
  await expect(card).toBeVisible();
  return card;
}

/** 打开详情页后切到「龙虎榜」tab。 */
async function openDragonTigerTab(page: Page) {
  const card = await openRelatedEvents(page);
  await card.locator(".ant-segmented-item").filter({ hasText: "龙虎榜" }).click();
  return card;
}

/**
 * 造一条**契约形状**的龙虎榜行（键集必须与真实端点一致，否则 mock 只锁得住前端自己的假设）：
 * 优先实抓一条活响应再改写 symbol/ts_code，端点不可用时退回手写行。
 */
async function dragonTigerRow(page: Page, symbol: string): Promise<Record<string, unknown>> {
  const resp = await page.request.get("/api/v1/market/dragon-tiger?limit=1");
  const live = resp.ok() ? ((await resp.json()) as Record<string, unknown>[]) : [];
  const base = live[0] ?? {
    trade_date: "2026-10-13",
    ts_code: "600519.SH",
    symbol: "600519",
    name: "贵州茅台",
    close: 1329.0,
    pct_change: 1.22,
    turnover_rate: 0.31,
    amount: 5_076_000_000,
    l_buy: 1_000_000_000,
    l_sell: 900_000_000,
    l_amount: 1_900_000_000,
    net_amount: 100_000_000,
    reason: "日涨幅偏离值达7%的证券",
  };
  return { ...base, ts_code: `${symbol}.SH`, symbol, name: "贵州茅台" };
}

test("龙虎榜请求真的带上 symbol=600519（不再是全市场榜单前端筛）", async ({ page }) => {
  const dragonTigerUrls: string[] = [];
  // 必须先注册再 goto：首个请求若在监听器挂上之前发出，就会漏记成假红
  page.on("request", (request) => {
    if (request.url().includes("/dragon-tiger")) dragonTigerUrls.push(request.url());
  });

  const card = await openRelatedEvents(page);
  expect(
    dragonTigerUrls,
    "打开详情页时还不该有龙虎榜请求（该 tab 用 enabled 懒加载）",
  ).toEqual([]);

  await card.locator(".ant-segmented-item").filter({ hasText: "龙虎榜" }).click();

  await expect
    .poll(() => dragonTigerUrls.length, { message: "点开龙虎榜后应发出 1 次 /dragon-tiger 请求" })
    .toBeGreaterThan(0);

  const symbolFiltered = dragonTigerUrls.filter((url) => /[?&]symbol=600519(&|$)/.test(url));
  expect(
    symbolFiltered,
    `龙虎榜请求未见 symbol=600519（实际：${JSON.stringify(dragonTigerUrls)}）`,
  ).not.toHaveLength(0);
});

test("mock 500 时显示错误 + 重试（不是「暂无上榜记录」），重试后错误消失", async ({ page }) => {
  // 必须先 mock 再点 tab，否则首个请求会打到真后端
  await page.route(DRAGON_TIGER_GLOB, fail500);

  const card = await openDragonTigerTab(page);

  const errorBlock = card.getByTestId("related-events-error");
  await expect(errorBlock, "500 时应显示行内错误块").toBeVisible();
  // 注意：antd Button 对两个汉字会插空格（无障碍名是「重 试」），故用 testid 定位 + 正则校验文案
  const retry = card.getByTestId("related-events-retry");
  await expect(retry, "错误块必须带可点的「重试」").toBeVisible();
  await expect(retry).toHaveText(/重\s*试/);
  // 判别性断言：失败**绝不能**被写成「没有记录」
  // 断在 rc-table 的空态占位行（`.ant-table-placeholder`）而不是裸文本：错误文案里可能引用该短语
  await expect(
    card.locator(".ant-table-placeholder"),
    "失败态复用了空态文案 = 把失败伪装成无记录",
  ).toBeHidden();

  // 恢复成「真的按本股过滤过」的 200：错误块必须消失，且表格渲染出本股那一行
  const row = await dragonTigerRow(page, "600519");
  await page.unroute(DRAGON_TIGER_GLOB);
  await page.route(DRAGON_TIGER_GLOB, (route) => fulfillJson(route, [row]));
  await retry.click();
  await expect(errorBlock, "重试后不应再显示错误块").toBeHidden();
  await expect(card.getByText(String(row.reason))).toBeVisible();
});

test("接口有返回却筛不出本股时，报「未按本股过滤」而不是「暂无上榜记录」", async ({ page }) => {
  // 复刻今天的 :80 现实：后端忽略 symbol，回的是全市场行
  const foreign = await dragonTigerRow(page, "000001");
  await page.route(DRAGON_TIGER_GLOB, (route) => fulfillJson(route, [foreign]));

  const card = await openDragonTigerTab(page);

  const errorBlock = card.getByTestId("related-events-error");
  await expect(errorBlock, "有返回但无本股行 = 后端没按 symbol 过滤，必须响亮失败").toBeVisible();
  await expect(card.getByText("返回结果未按本股过滤")).toBeVisible();
  await expect(card.getByTestId("related-events-retry")).toBeVisible();
  // 这一条是本用例的核心：护栏不得把「服务端没筛」说成「本股没上榜」
  await expect(
    card.locator(".ant-table-placeholder"),
    "护栏把服务端没筛伪装成「本股没上榜」",
  ).toBeHidden();
});

test("200 + 空数组仍是「暂无上榜记录」（成功为空不得升级成错误态）", async ({ page }) => {
  await page.route(DRAGON_TIGER_GLOB, (route) => fulfillJson(route, []));

  const card = await openDragonTigerTab(page);

  await expect(
    card.locator(".ant-table-placeholder"),
    "真·空结果必须保持空态文案",
  ).toHaveText(/暂无上榜记录/);
  await expect(card.getByTestId("related-events-error"), "空结果不是错误").toBeHidden();
});

test("重试按钮在请求中禁用：连点两次只发一次请求", async ({ page }) => {
  await page.route(DRAGON_TIGER_GLOB, fail500);
  const card = await openDragonTigerTab(page);
  const errorBlock = card.getByTestId("related-events-error");
  await expect(errorBlock).toBeVisible();

  // 换成慢响应（400ms），让「请求在途」这个窗口足够长，可稳定复现连点
  let refires = 0;
  await page.unroute(DRAGON_TIGER_GLOB);
  await page.route(DRAGON_TIGER_GLOB, async (route) => {
    refires += 1;
    await new Promise((resolve) => setTimeout(resolve, 400));
    await fulfillJson(route, []);
  });

  const retry = card.getByTestId("related-events-retry");
  await retry.click();
  // 在途态：loading 类 + 真 disabled（antd Button 的 loading 只拦 onClick，disabled 才拦原生事件）
  await expect(retry, "请求在途时「重试」应进入 loading/禁用态").toBeDisabled();
  await expect(retry).toHaveClass(/ant-btn-loading/);
  await retry.click({ force: true }); // 已禁用 → 浏览器不派发 click，不应发起第二次请求
  await expect(errorBlock).toBeHidden();
  expect(refires, "连点「重试」只应发 1 次请求").toBe(1);
});

/**
 * 五个 tab **逐一**验证同一契约（brief 验收：mock 500 时 5 个 tab 全部显示错误 + 可重试）。
 * 每个用例各自 goto，是为了让 `page.route` 严格先于**首个**请求（公告 tab 是默认激活的）。
 * 重试后的 200 用 `[]` 兜底（而非 unroute 打活栈）：本组只验「错误态可退出」，不依赖线上数据形态。
 */
const TAB_CASES = [
  { label: "公告", glob: "**/announcements**", emptyText: "暂无相关公告" },
  { label: "龙虎榜", glob: "**/dragon-tiger**", emptyText: "暂无上榜记录" },
  { label: "大宗", glob: "**/block-trades**", emptyText: "暂无大宗交易记录" },
  { label: "解禁", glob: "**/share-floats**", emptyText: "暂无解禁记录" },
  { label: "回购", glob: "**/repurchases**", emptyText: "暂无回购记录" },
] as const;

for (const tab of TAB_CASES) {
  test(`「${tab.label}」失败时显示错误 + 重试，而不是「${tab.emptyText}」`, async ({ page }) => {
    await page.route(tab.glob, fail500);

    const card = await openRelatedEvents(page);
    await card.locator(".ant-segmented-item").filter({ hasText: tab.label }).click();

    const errorBlock = card.getByTestId("related-events-error");
    await expect(errorBlock, `「${tab.label}」500 时应显示行内错误块`).toBeVisible();
    await expect(card.getByTestId("related-events-retry")).toBeVisible();
    await expect(
      card.locator(".ant-table-placeholder"),
      `「${tab.label}」把失败写成了「${tab.emptyText}」= 缺失 ≠ 0 被违反`,
    ).toBeHidden();

    await page.unroute(tab.glob);
    await page.route(tab.glob, (route) => fulfillJson(route, []));
    await card.getByTestId("related-events-retry").click();
    await expect(errorBlock, `「${tab.label}」重试后错误块应消失`).toBeHidden();
  });
}

test("数据面「龙虎榜」不带 symbol（另一调用点 `fetchDragonTiger(15)` 行为不变）", async ({ page }) => {
  const dragonTigerUrls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/dragon-tiger")) dragonTigerUrls.push(request.url());
  });

  await page.goto("/market");
  await page.getByRole("tab", { name: "数据面" }).click();
  await page.getByRole("tab", { name: "龙虎榜", exact: true }).click();
  const board = page.locator(".ant-card").filter({ hasText: "数据面" });
  await expect(board.locator("thead th").filter({ hasText: "上榜原因" })).toBeVisible();
  await expect
    .poll(() => dragonTigerUrls.length, { message: "数据面应发出全市场榜单请求" })
    .toBeGreaterThan(0);

  const withSymbol = dragonTigerUrls.filter((url) => /[?&]symbol=/.test(url));
  expect(
    withSymbol,
    `全市场榜单调用点不该带 symbol（实际：${JSON.stringify(dragonTigerUrls)}）`,
  ).toEqual([]);
});
