import { test, expect as baseExpect } from "@playwright/test";

/**
 * P0-3 E2E：个股详情「相关数据」的两条硬约束。
 *
 * 1. **失败 ≠ 空**（F3）：五个 tab 过去统一 `dataSource={q.data ?? []} + loading={q.isLoading}`，
 *    500/断网时 `data` 仍是 `undefined`，表格直接落进 `locale.emptyText`，页面于是宣称
 *    「暂无上榜记录」——一次失败被伪装成一条事实。断言：mock 500 时**错误块 + 重试**可见，
 *    且该 tab 的空态文案**不可见**；unroute 后点重试，错误块消失。
 * 2. **龙虎榜按标的上送**（P0-2 前端半）：`fetchDragonTiger(limit, symbol?)` 必须把 symbol 送出去，
 *    由服务端过滤（可跨交易日）；前端 `.filter()` 只是兜底护栏，不再是唯一过滤手段。
 *
 * 断言纪律：
 * - `page.on("request")` / `page.route(...)` 必须在 `goto` 或触发查询（点 tab）**之前**注册，
 *   否则首个请求漏记 / 打到真后端；
 * - mock 每个用例只落在**一个**端点上，不碰其余端点（它们的真失败与本用例结论无关）；
 * - 不断言行数：运行中的后端可能还没部署 `symbol` 参数（后端半在另一 agent 手里），
 *   所以「请求 URL 带 symbol」才是判别性证据，行数不是。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const DRAGON_TIGER_GLOB = "**/dragon-tiger**";

/** 相关数据卡的作用域：避免与市场页的「龙虎榜」tab、脚注文案撞 strict mode。 */
function relatedEventsCard(page: import("@playwright/test").Page) {
  return page.locator(".ant-card").filter({ hasText: "相关数据" });
}

/** 打开详情页并等到「相关数据」卡就绪；个股缺失时跳过（与 marketDataFace.spec.ts 同策略）。 */
async function openRelatedEvents(page: import("@playwright/test").Page) {
  await page.goto("/stock/600519");
  const card = relatedEventsCard(page);
  const notFound = page.getByText("未找到该股票");
  await expect(notFound.or(card).first()).toBeVisible();
  test.skip(await notFound.isVisible(), "个股 600519 数据缺失，跳过");
  await expect(card).toBeVisible();
  return card;
}

function clickDragonTiger(card: ReturnType<typeof relatedEventsCard>) {
  return card.locator(".ant-segmented-item").filter({ hasText: "龙虎榜" }).click();
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

  await clickDragonTiger(card);

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
  await page.route(DRAGON_TIGER_GLOB, (route) => route.fulfill({ status: 500, body: "boom" }));

  const card = await openRelatedEvents(page);
  await clickDragonTiger(card);

  const errorBlock = card.getByTestId("related-events-error");
  await expect(errorBlock, "500 时应显示行内错误块").toBeVisible();
  // 注意：antd Button 对两个汉字会插空格（无障碍名是「重 试」），故用 testid 定位 + 正则校验文案
  const retry = card.getByTestId("related-events-retry");
  await expect(retry, "错误块必须带可点的「重试」").toBeVisible();
  await expect(retry).toHaveText(/重\s*试/);
  // 判别性断言：失败**绝不能**被写成「没有记录」
  await expect(page.getByText("暂无上榜记录"), "失败态复用了空态文案 = 把失败伪装成无记录").toBeHidden();

  // 恢复网络后重试：错误块必须消失（行或真·空态都接受 —— 后端数据不可控）
  await page.unroute(DRAGON_TIGER_GLOB);
  await retry.click();
  await expect(errorBlock, "重试后不应再显示错误块").toBeHidden();
});

/**
 * 五个 tab **逐一**验证同一契约（brief 验收：mock 500 时 5 个 tab 全部显示错误 + 可重试）。
 * 每个用例各自 goto，是为了让 `page.route` 严格先于**首个**请求（公告 tab 是默认激活的）。
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
    await page.route(tab.glob, (route) => route.fulfill({ status: 500, body: "boom" }));

    const card = await openRelatedEvents(page);
    await card.locator(".ant-segmented-item").filter({ hasText: tab.label }).click();

    const errorBlock = card.getByTestId("related-events-error");
    await expect(errorBlock, `「${tab.label}」500 时应显示行内错误块`).toBeVisible();
    await expect(card.getByTestId("related-events-retry")).toBeVisible();
    await expect(
      page.getByText(tab.emptyText),
      `「${tab.label}」把失败写成了「${tab.emptyText}」= 缺失 ≠ 0 被违反`,
    ).toBeHidden();

    await page.unroute(tab.glob);
    await card.getByTestId("related-events-retry").click();
    await expect(errorBlock, `「${tab.label}」重试后错误块应消失`).toBeHidden();
  });
}
