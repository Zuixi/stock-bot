import { test, expect as baseExpect, type Page, type Route } from "@playwright/test";

/**
 * 生产构建里 `import.meta.env.DEV` 被替换为 false ⇒ `KlineChart` 不注入
 * `window.__klineEchartsInstance`，读 ECharts 实例的三条用例在部署栈上必然失败。
 *
 * 这不是缺陷，是本文件头写明的约束（本组用例要求跑在 Vite dev server 上）。为了避免
 * 有人在 `E2E_BASE_URL=http://127.0.0.1`（部署栈）上跑到三条红就以为产品坏了，把这件事
 * 做成**显式声明**：只有在明确"我在测构建产物"（`E2E_PROD_STACK=1`）时才跳过，
 * 其余情况仍然硬失败——正常路径（dev server）不会因为这条降级而放过真回归。
 */
const PROD_STACK = process.env.E2E_PROD_STACK === "1";
const DEV_HANDLE_SKIP = "读 ECharts 实例需要 dev 句柄；生产构建不注入。改用 E2E_BASE_URL=…:3010（见文件头）";

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

/**
 * ── P1-2：事件标记 + 列表定位联动 ──────────────────────────────────────────────
 *
 * 三条用例，各钉一个可被机械否证的事实：
 *
 * 1. **同一份数据只取一次**：K 线标记要消费公告/龙虎榜等事件，列表也要渲染它们。
 *    两边必须命中**同一组 React Query 键**（`useStockEvents` 是唯一取数点），
 *    否则同一个端点会被请求两次、且图与列表可能来自不同时刻的响应。
 * 2. **点行真的移动了视窗**：断言不是"按钮响了"，而是 ECharts 实例上
 *    `dataZoom` 的真实 `startValue/endValue` 变成了该日 ±20 根（读实例 option），
 *    外加用户可见的 ribbon 日期变成该事件日。
 * 3. **气泡真的进了 option**：`series[0].markPoint.data` 里有该日、且坐标落在该日
 *    （不是"最近一根"——那正是要修的"缺失 ≠ 0"式谎言）。
 *
 * 读实例的通道：`KlineChart` 在 **dev 下**把实例挂到 `window.__klineEchartsInstance`
 * （生产构建 no-op，见组件内注释）。气泡与缩放窗口都画在 canvas 里，DOM 没有对应
 * 节点；echarts 的实例注册表是模块私有的，`page.evaluate` 里拿不到 `echarts` 模块
 * ⇒ 不挂这个 dev 句柄就只能写"断言 DOM 里有个 canvas"这种同义反复。
 * 因此本组用例要求 e2e 跑在 Vite dev server 上（`E2E_BASE_URL=...:3010`）。
 */

/**
 * 「公告日期必须落在 K 线区间内」的确定性造数：先按**与前端同一条查询**取回 bar 日期，
 * 再用其中某个真实交易日构造公告。比猜"最近某个交易日"稳，也比依赖线上公告数据稳
 * （线上公告的日期未必是 bar 日期，上市前/周末都会被正确地丢掉，测不出标记）。
 */
async function fetchBarDates(page: Page): Promise<string[]> {
  const end = new Date();
  const start = new Date();
  start.setDate(end.getDate() - 3650);
  const iso = (d: Date) => d.toISOString().slice(0, 10);
  const resp = await page.request.get(
    `/api/v1/exchanges/Shanghai_Stocks/stocks/600519/quotes/daily?start=${iso(start)}&end=${iso(end)}&adjust=qfq`,
  );
  expect(resp.ok(), "K 线端点不可用，无法构造落在 bar 区间内的事件").toBeTruthy();
  const body = (await resp.json()) as { data?: Array<{ trade_date: string }> };
  return (body.data ?? []).map((row) => row.trade_date);
}

/** 契约形状的公告行（键集与后端 `AnnouncementOut` 一致，见 `mapAnnouncement`）。 */
function announcementRow(symbol: string, date: string) {
  return {
    announcement_id: `e2e-${date}`,
    sec_code: symbol,
    sec_name: "贵州茅台",
    title: "关于2026年第三季度报告的公告",
    announce_time: `${date}T16:00:00`,
    category: "event",
    pdf_url: null,
  };
}

interface ChartSnapshot {
  /** 已加载的 bar 日期（xAxis[0].data） */
  dates: string[];
  /** dataZoom[0] 的真实起止（dataIndex，非百分比） */
  start: number | null;
  end: number | null;
  markers: Array<{ name: string | null; date: string | null }>;
}

/**
 * 读 ECharts 实例上的真实状态（dev 句柄）。返回 `null` = 句柄还没挂上（图未 ready）。
 * 只回传原始值，不回传整个 option：option 里含 formatter 函数，无法结构化克隆。
 */
async function readChart(page: Page): Promise<ChartSnapshot | null> {
  return page.evaluate(() => {
    const win = window as unknown as {
      __klineEchartsInstance?: { getOption: () => any };
    };
    const instance = win.__klineEchartsInstance;
    if (!instance) return null;
    const option = instance.getOption();
    const zoom = (option.dataZoom ?? [])[0] ?? {};
    const dates = ((option.xAxis ?? [])[0]?.data ?? []) as string[];
    const markPoint = (option.series ?? [])[0]?.markPoint;
    const markers = ((markPoint?.data ?? []) as Array<{ name?: string; coord?: unknown[] }>).map((item) => ({
      name: item.name ?? null,
      date: Array.isArray(item.coord) ? String(item.coord[0]) : null,
    }));
    return {
      dates,
      start: typeof zoom.startValue === "number" ? zoom.startValue : null,
      end: typeof zoom.endValue === "number" ? zoom.endValue : null,
      markers,
    };
  });
}

/** 等图真的画出数据（ribbon 有日期）且 dev 句柄可用。 */
async function waitForChart(page: Page): Promise<ChartSnapshot> {
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card.getByTestId("kline-ribbon-date")).toBeVisible();
  await expect
    .poll(async () => Boolean(await readChart(page)), { message: "K 线实例未就绪（dev 句柄缺失）" })
    .toBe(true);
  const chart = await readChart(page);
  expect(chart, "K 线实例未就绪").not.toBeNull();
  return chart as ChartSnapshot;
}

test("页面与列表共用同一份查询：/announcements 与 /dragon-tiger 各恰好 1 次", async ({ page }) => {
  const annUrls: string[] = [];
  const dragonUrls: string[] = [];
  // 必须先注册再 goto：announcements 在挂载时立刻发出
  page.on("request", (request) => {
    const url = request.url();
    if (url.includes("/announcements")) annUrls.push(url);
    if (url.includes("/dragon-tiger")) dragonUrls.push(url);
  });

  // 两个端点都 mock 成「成功且有本股行」：把请求数从**失败重试**里隔离出来
  // （QueryClient 默认 `retry: failureCount < 1`，一次 500 会让计数变 2，与本用例无关）。
  const row = await dragonTigerRow(page, "600519");
  await page.route("**/announcements**", (route) => fulfillJson(route, [announcementRow("600519", "2026-10-16")]));
  await page.route(DRAGON_TIGER_GLOB, (route) => fulfillJson(route, [row]));

  const card = await openRelatedEvents(page);
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);
  await expect.poll(() => annUrls.length, { message: "首屏应发出 1 次 /announcements" }).toBe(1);
  expect(dragonUrls, "打开详情页时还不该有龙虎榜请求（该 tab 用 enabled 懒加载）").toEqual([]);

  await card.locator(".ant-segmented-item").filter({ hasText: "龙虎榜" }).click();
  await expect.poll(() => dragonUrls.length, { message: "点开龙虎榜应发出 1 次请求" }).toBe(1);
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);

  // 切回公告：两个查询都有缓存（staleTime 5min），不该再有任何一次网络请求
  await card.locator(".ant-segmented-item").filter({ hasText: "公告" }).click();
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(annUrls, `切回公告 tab 不应重复请求（实际：${JSON.stringify(annUrls)}）`).toHaveLength(1);
  expect(
    dragonUrls,
    `页面（K 线标记）与列表必须共用同一查询键，不应重复请求（实际：${JSON.stringify(dragonUrls)}）`,
  ).toHaveLength(1);
});

test("点击事件行 → K 线视窗移到该日 ±20 根并高亮该日", async ({ page }) => {
  test.skip(PROD_STACK, DEV_HANDLE_SKIP);
  const bars = await fetchBarDates(page);
  // 取一个「既在默认视窗之外（最近 120 根）又至少有 20 根左侧邻居」的交易日：
  // 前者让"视窗移动"可判定，后者让 idx±20 不被左边界截断（否则窗口宽度不是 41）。
  test.skip(bars.length < 240, "K 线样本不足 240 根，无法构造两侧都够 20 根的定位目标");
  const target = bars[100];
  test.skip(!target, "K 线样本不足，无法构造落在区间内的公告日期");
  await page.route("**/announcements**", (route) => fulfillJson(route, [announcementRow("600519", target)]));

  await page.goto("/stock/600519");
  const card = await openRelatedEvents(page);
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);
  const before = await waitForChart(page);

  // 目标日在图上的真实下标：用它算期望视窗，而不是猜
  const idx = before.dates.indexOf(target);
  expect(idx, `mock 的公告日 ${target} 不在已加载 bar 内（图上第一根 ${before.dates[0]}）`).toBeGreaterThan(-1);
  expect(before.start, "初始视窗的 startValue 读不到（dataZoom 形状变了？）").not.toBeNull();
  expect(before.end, "初始视窗的 endValue 读不到").not.toBeNull();
  // 前提检查：目标日在默认视窗之外，否则"视窗移动"无从判定
  expect(idx, "目标日落在默认视窗内，构造失效").toBeLessThan(before.start as number);

  // 点「时间」单元格而不是标题链接：标题是外链 <a>，点了会开新标签页
  await card.locator("tbody tr.ant-table-row").first().locator("td").first().click();

  await expect
    .poll(async () => (await readChart(page))?.start ?? null, { message: "点击后 dataZoom 未变化" })
    .not.toBe(before.start);

  const after = await readChart(page);
  expect(after).not.toBeNull();
  const zoom = after as ChartSnapshot;
  // 视窗包含该日（ECharts 可能对类目轴做吸附，故用区间而不是逐字相等）
  expect(zoom.start as number, `视窗起点 ${zoom.start} 应 ≤ ${idx}`).toBeLessThanOrEqual(idx);
  expect(zoom.end as number, `视窗终点 ${zoom.end} 应 ≥ ${idx}`).toBeGreaterThanOrEqual(idx);
  // ±20 根：窗口宽度应约 41 根（允许 1 根吸附误差）
  const width = (zoom.end as number) - (zoom.start as number) + 1;
  expect(width, `定位后窗口为 ${width} 根，期望约 41 根`).toBeGreaterThanOrEqual(40);
  expect(width, `定位后窗口为 ${width} 根，期望约 41 根`).toBeLessThanOrEqual(42);

  // 用户可见的高亮：ribbon 日期变成该事件日（canvas 上的高亮看不见，读数行看得见）。
  // ribbon 在「历史行情」卡里，不在「相关数据」卡里，故用页面级 testid（全页唯一）。
  await expect(page.getByTestId("kline-ribbon-date")).toHaveText(target);
});

test("周K 下点事件行**不跳**（日线下标不得套到聚合序列），切回日K 才定位", async ({ page }) => {
  test.skip(PROD_STACK, DEV_HANDLE_SKIP);
  const bars = await fetchBarDates(page);
  test.skip(bars.length < 240, "K 线样本不足 240 根，无法构造有效的日线下标");
  const target = bars[100];
  await page.route("**/announcements**", (route) => fulfillJson(route, [announcementRow("600519", target)]));

  await page.goto("/stock/600519");
  const card = await openRelatedEvents(page);
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);

  const daily = await waitForChart(page);
  const dailyIdx = daily.dates.indexOf(target);
  expect(dailyIdx, "mock 的公告日不在日线序列里").toBeGreaterThan(-1);

  const history = page.locator(".ant-card").filter({ hasText: "历史行情" });
  const seg = (text: string) => history.locator(".ant-segmented-item").filter({ hasText: text });
  const ribbonDate = page.getByTestId("kline-ribbon-date");
  const rowDateCell = card.locator("tbody tr.ant-table-row").first().locator("td").first();

  // 切周K：序列被聚合成更短的一维，x 轴日期随之变化（等它真的生效再断言）
  await seg("周K").click();
  await expect
    .poll(async () => (await readChart(page))?.dates.length ?? 0, { message: "周K 未生效" })
    .toBeLessThan(daily.dates.length);
  const weekly = (await readChart(page)) as ChartSnapshot;
  const ribbonBefore = (await ribbonDate.innerText()).trim();

  /**
   * 旧实现的 bug：`dailyIdx`（≈100）被 clamp 到周线序列上，视窗会从尾部跳到 [80,120]，
   * 高亮与 ribbon 也会落到**另一周**——比"不跳"更糟的静默事实错误。
   * 负向断言需要给 React effect 一个落地窗口（handler 是同步的，600ms 是数量级上的余量）；
   * 紧跟着的正对照（切回日K 再点）证明"这一次点击本来会生效"。
   */
  await rowDateCell.click();
  await page.waitForTimeout(600);

  const weeklyAfter = (await readChart(page)) as ChartSnapshot;
  expect(weeklyAfter.start, "周K 下视窗不应移动（日线下标不能套到聚合序列）").toBe(weekly.start);
  expect(weeklyAfter.end, "周K 下视窗不应移动").toBe(weekly.end);
  await expect(ribbonDate, "周K 下 ribbon 不应显示一个错误日期").toHaveText(ribbonBefore);

  // 正对照：切回日K 再点同一行 → 必须真的定位到该日 ±20 根
  await seg("日K").click();
  await expect
    .poll(async () => (await readChart(page))?.dates.length ?? 0, { message: "日K 未恢复" })
    .toBe(daily.dates.length);
  await rowDateCell.click();
  await expect
    .poll(async () => {
      const chart = await readChart(page);
      return chart ? (chart.start as number) <= dailyIdx && dailyIdx <= (chart.end as number) : false;
    }, { message: "切回日K 后仍未定位到事件日" })
    .toBe(true);
  await expect(ribbonDate).toHaveText(target);
});

test("事件日画出 markPoint 气泡：坐标落在该日，不是最近一根", async ({ page }) => {
  test.skip(PROD_STACK, DEV_HANDLE_SKIP);
  const bars = await fetchBarDates(page);
  // 取靠后的交易日：落在默认视窗内，气泡可见
  const target = bars[bars.length - 3];
  test.skip(!target, "K 线样本不足，无法构造落在区间内的公告日期");
  await page.route("**/announcements**", (route) => fulfillJson(route, [announcementRow("600519", target)]));

  await page.goto("/stock/600519");
  const card = await openRelatedEvents(page);
  await expect(card.locator("tbody tr.ant-table-row")).toHaveCount(1);
  await waitForChart(page);

  await expect
    .poll(async () => (await readChart(page))?.markers.length ?? 0, { message: "K 线上没有出现事件气泡" })
    .toBe(1);

  const chart = (await readChart(page)) as ChartSnapshot;
  expect(chart.markers, `气泡应只有该公告日一枚（实际：${JSON.stringify(chart.markers)}）`).toEqual([
    { name: "公告", date: target },
  ]);
  // 判别性：气泡日期必须真的在已加载的 bar 里（否则它会被画到错误位置/被丢掉）
  expect(chart.dates).toContain(target);

  /**
   * 点开另一个 tab：同一批查询里多了一类**已加载**数据 ⇒ 图上多一枚气泡，
   * 但不会为此多发一个请求（那一次请求本来就是列表的，页面的标记只是消费它的结果）。
   * 这是"五类事件共用一份查询"在图上的可见证据。
   */
  const dragonDate = bars[bars.length - 5];
  const dragon = { ...(await dragonTigerRow(page, "600519")), trade_date: dragonDate };
  await page.route(DRAGON_TIGER_GLOB, (route) => fulfillJson(route, [dragon]));
  const dragonRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/dragon-tiger")) dragonRequests.push(request.url());
  });

  await card.locator(".ant-segmented-item").filter({ hasText: "龙虎榜" }).click();
  await expect
    .poll(async () => (await readChart(page))?.markers.length ?? 0, { message: "龙虎榜加载后图上没多出气泡" })
    .toBe(2);

  const both = (await readChart(page)) as ChartSnapshot;
  expect(both.markers.map((m) => m.name)).toEqual(expect.arrayContaining(["公告", "龙虎榜"]));
  expect(both.markers.map((m) => m.date)).toEqual(expect.arrayContaining([target, dragonDate]));
  expect(dragonRequests, "图上多一枚气泡不应额外请求事件数据").toHaveLength(1);
});
