import { test, expect as baseExpect, type Page, type Route } from "@playwright/test";

/**
 * P2-1 E2E：详情页的**分块容错**（失败 ≠ 无记录 / 固定高度占位 / 块间隔离）。
 *
 * 五条用例，每条只钉一个可被机械否证的事实：
 *
 * 1. **块间隔离（取数失败）**：把 `/financial-summary` 打成 500，只有「成长与盈利」这一块
 *    显示「加载失败 + 重试」；K 线与报价条照常渲染（页面没被整块换掉），且该块**不**出现
 *    「暂无…」——失败被写成"没有财报数据"正是本任务要消灭的那类谎言。
 * 2. **重试真的重取**：恢复端点后点「重试」，错误块消失、数值真的上屏（不是把按钮藏了）。
 * 3. **零布局抖动（CLS）**：用**闸门 promise**（不是 sleep）把 `/financial-summary` 与
 *    `/quotes/daily` 按在加载态，记录事件卡顶边的 `boundingBox().y`；放闸、等全部请求落地
 *    再记一次，断言位移 ≤2px。闸门关闭时骨架必须在（否则"零位移"可能只是"早就加载完了"）。
 * 4. **加载中不是空态**：同一份被闸住的响应，先断言该块**没有**「暂无…」，放闸成
 *    「200 + 三格全空」后，同一个块上才出现「暂无财报数据」——两种状态在同一元素上可判。
 * 5. **块间隔离（渲染异常）**：让成长卡读到 `value: "boom"`（字符串当数字用 ⇒ 渲染期
 *    `TypeError`），断言只有这一块被 `BlockBoundary` 换成错误卡，K 线 / 报价条 / 估值卡 /
 *    公司概况 / 事件表全部还在（React 18 单个组件抛错会卸载整棵子树，这是 blast radius 的判据）。
 * 6. **K 线块失败 ≠ 无记录**：`/quotes/daily` 打 500 时，K 线卡必须给「加载失败 + 重试」，
 *    **不得**落到 `<Empty>暂无K线数据</Empty>` —— 旧实现只解构 `{data,isLoading}`，于是同一屏
 *    上报价条已经报「加载失败」（同一个缓存键）、K 线卡却宣称"这只票没有 K 线"。
 * 7. **K 线重试真的重取**：恢复 `/quotes/daily` 后点重试，错误块消失、ribbon 重新出现。
 *
 * 断言纪律（与 `stockDetailLayout.spec.ts` / `stockDetailFinancial.spec.ts` 同）：
 * `page.route` / `page.on("request")` 必须在 `goto` 之前注册；锚点用页面上真实出现的元素，
 * 不用固定 sleep；每个用例只打瘫**一个**端点，不碰其余端点。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const STOCK = "/stock/600519";
const WIDE = { width: 1440, height: 900 };
const SUMMARY_GLOB = "**/financial-summary*";
const DAILY_GLOB = "**/quotes/daily*";

const fulfillJson = (route: Route, body: unknown) =>
  route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });

/** 成长卡（错误态时 Alert 在卡内，标题仍是「成长与盈利」，故这个锚点三态都能命中）。 */
const growthCard = (page: Page) => page.locator(".ant-card").filter({ hasText: "成长与盈利" });
const klineCard = (page: Page) => page.locator(".ant-card").filter({ hasText: "历史行情" });
const eventsCard = (page: Page) => page.locator(".ant-card").filter({ hasText: "相关数据" });

/** 与 `stockDetailFinancial.spec.ts` 同形的 `financial-summary` 响应：`metrics` 逐字传入。 */
function summaryPayload(metrics: Record<string, unknown>) {
  return {
    exchange: "Shanghai_Stocks",
    symbol: "600519",
    name: "贵州茅台",
    latest_period: null,
    report_type: null,
    ann_date: null,
    as_of: null,
    source: null,
    metrics,
  };
}

/** 闸门：请求进 route 但**不放行**，直到测试主动 release（比固定 sleep 稳定）。 */
function makeGate() {
  let release!: () => void;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  return { gate, release: () => release() };
}

async function openDetail(page: Page) {
  await page.goto(STOCK);
  await expect(page.getByTestId("quote-bar")).toBeVisible();
}

test("一块取数失败只坏一块：成长与盈利显示「加载失败 + 重试」，K 线与报价条仍在", async ({
  page,
}) => {
  await page.setViewportSize(WIDE);
  // 只打瘫财务摘要：模拟"这一块坏了"，而不是"整页坏了"
  await page.route(SUMMARY_GLOB, (route) => route.fulfill({ status: 500, body: "boom" }));

  await openDetail(page);

  const card = growthCard(page);
  await expect(card, "成长与盈利卡本身应仍在（失败是块内的错误态，不是整块消失）").toBeVisible();

  const errorBlock = card.getByTestId("growth-card-error");
  await expect(errorBlock, "500 时卡内应显示错误块").toBeVisible();
  await expect(errorBlock).toContainText("加载失败");

  // 「重试」必须真的可点（antd 对两个汉字会插空格，故用正则匹配无障碍名）
  const retry = card.getByTestId("growth-card-retry");
  await expect(retry).toBeVisible();
  await expect(retry).toContainText(/重\s*试/);

  // 判别性断言：失败**不得**被写成"没有财报数据"
  await expect(card, "失败被伪装成「暂无财报数据」").not.toContainText("暂无");

  // 页面没有空白：另外三块照常渲染（这正是"分块边界"与"整页边界"的区别）
  await expect(page.getByTestId("quote-bar"), "报价条被一块的失败带走了").toBeVisible();
  await expect(klineCard(page), "K 线卡被一块的失败带走了").toBeVisible();
  await expect(eventsCard(page), "事件表被一块的失败带走了").toBeVisible();
  await expect(page.getByTestId("company-profile")).toBeVisible();
});

test("恢复端点后点「重试」：错误块消失、真数值上屏", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await page.route(SUMMARY_GLOB, (route) => route.fulfill({ status: 500, body: "boom" }));

  await openDetail(page);
  const card = growthCard(page);
  const errorBlock = card.getByTestId("growth-card-error");
  await expect(errorBlock).toBeVisible();

  // 换回真后端再点重试：证明重试是真的重取，不是只把错误块藏起来
  await page.unroute(SUMMARY_GLOB);
  await card.getByTestId("growth-card-retry").click();

  await expect(errorBlock, "重试成功后不应再有错误块").toBeHidden();
  // 600519 的三项在真后端有值（`stockDetailFinancial.spec.ts` 同样依赖这一点）：
  // 恢复后 ROE 必须是数字形态，不能停在 `--`
  await expect(card, "重试后仍停在骨架（--），说明没有真的重取").toContainText(/\d+\.\d+%/);
  await expect(card).not.toContainText("暂无");
});

test("零布局抖动：加载态与落地后事件卡顶边位移 ≤2px（闸门 promise，不是 sleep）", async ({
  page,
}) => {
  await page.setViewportSize(WIDE);

  // 同时闸住两块的数据源：
  // - `/financial-summary` → 成长与盈利（骨架 `--` → 三格 + 质量徽标，卡高 128→152）
  // - `/quotes/daily`      → 股性事实行（整行不渲染 → 六枚 chip + 一句人话，0→93）
  // 这两处正是改造前把事件卡往下推的两股力。
  const { gate, release } = makeGate();
  await page.route(SUMMARY_GLOB, async (route) => {
    await gate;
    await route.continue();
  });
  await page.route(DAILY_GLOB, async (route) => {
    await gate;
    await route.continue();
  });

  await openDetail(page);
  const events = eventsCard(page);
  await expect(events, "事件卡应在加载态就存在（否则下面测的是别的东西）").toBeVisible();

  // 先证伪"其实早就加载完了"：闸门关着时两块的骨架必须都在
  await expect(page.getByTestId("character-facts-loading"), "闸门没关住：股性行已在加载完成态").toBeVisible();
  const card = growthCard(page);
  await expect(card).toContainText("--");
  await expect(card).toContainText("ROE");

  await page.evaluate(() => window.scrollTo(0, 0));
  const before = await events.boundingBox();
  expect(before, "加载态下量不到事件卡的位置").not.toBeNull();

  // 放闸：两份响应一起落地
  release();
  await page.waitForLoadState("networkidle").catch(() => {});
  // 落地锚点：股性行换成真内容、成长卡出数值（不是"网络空闲=已渲染"）
  await expect(page.getByTestId("character-facts")).toBeVisible();
  await expect(page.getByTestId("character-facts-loading")).toHaveCount(0);
  await expect(card).not.toContainText("--");

  await page.evaluate(() => window.scrollTo(0, 0));
  const after = await events.boundingBox();
  expect(after, "落地后量不到事件卡的位置").not.toBeNull();

  const delta = Math.abs(after!.y - before!.y);
  expect(
    delta,
    `事件卡顶边从 y=${before!.y} 移到 y=${after!.y}（位移 ${delta}px）—— ` +
      "加载态没有占住最终高度：成长卡要留出质量徽标行（152px），股性行要留出骨架（93px）",
  ).toBeLessThanOrEqual(2);
});

test("加载中不是空态：同一块先「无暂无」，成功后才是「暂无财报数据」", async ({ page }) => {
  await page.setViewportSize(WIDE);

  const { gate, release } = makeGate();
  // 放闸后回「200 + 三格全空」：成功且为空 = 真正的"无记录"
  await page.route(SUMMARY_GLOB, async (route) => {
    await gate;
    await fulfillJson(
      route,
      summaryPayload({ roe: { value: null, quality: null, source: null } }),
    );
  });

  await openDetail(page);
  const card = growthCard(page);
  await expect(card).toBeVisible();

  // 加载中：`--` 骨架在，但**绝不能**出现"暂无"（还没到 ≠ 没有）
  await expect(card, "加载中应显示骨架").toContainText("--");
  await expect(card, "加载中被写成了「暂无财报数据」").not.toContainText("暂无");
  await expect(card.getByTestId("growth-card-error"), "加载中不是失败").toBeHidden();

  // 放闸 → 成功且为空 → 这时才允许出现"暂无…"（同一元素上的可判差异）
  release();
  await expect(card.getByTestId("growth-card-empty")).toBeVisible();
  await expect(card.getByTestId("growth-card-empty")).toContainText("暂无");
  await expect(card.getByTestId("growth-card-error"), "成功为空不是失败").toBeHidden();
});

test("一块渲染异常只坏一块：成长卡被边界换成错误卡，其余块全部存活", async ({ page }) => {
  await page.setViewportSize(WIDE);
  // `value` 给了字符串：`GrowthValue` 会调 `value.toFixed` ⇒ 渲染期 TypeError。
  // 这是真实的"组件渲染抛错"，不是查询失败 —— 只有 `componentDidCatch` 能兜住。
  await page.route(SUMMARY_GLOB, (route) =>
    fulfillJson(route, summaryPayload({ roe: { value: "boom", quality: "reported", source: "tushare" } })),
  );

  await openDetail(page);

  const boundaryError = page.locator('[data-testid="block-boundary-error"]');
  await expect(boundaryError, "渲染异常没有被分块边界兜住（整页会被卸载）").toBeVisible();
  await expect(boundaryError).toHaveCount(1);
  await expect(boundaryError).toHaveAttribute("data-block", "成长与盈利");
  await expect(boundaryError).toContainText("加载失败");
  await expect(boundaryError.getByTestId("block-boundary-retry")).toContainText(/重\s*试/);

  // 其余块全部存活：React 18 里单个组件抛错会卸载整棵子树，这些断言是本用例的核心
  await expect(page.getByTestId("quote-bar")).toBeVisible();
  await expect(klineCard(page)).toBeVisible();
  await expect(page.getByTestId("valuation-card"), "同一父容器里的估值卡被牵连了").toBeVisible();
  await expect(page.getByTestId("company-profile")).toBeVisible();
  await expect(page.getByTestId("character-facts")).toBeVisible();
  await expect(eventsCard(page)).toBeVisible();
  // 崩掉的块不再显示自己的卡（错误卡取代了它，不是两张卡叠在一起）
  await expect(growthCard(page)).toHaveCount(0);
});

test("K 线取数失败不是「暂无K线数据」：图块显示「加载失败 + 重试」", async ({ page }) => {
  await page.setViewportSize(WIDE);
  // 只打瘫日线：K 线图、52 周区间、股性行共用这一个端点（同一缓存键），这正是旧实现的坑
  await page.route(DAILY_GLOB, (route) => route.fulfill({ status: 500, body: "boom" }));

  await openDetail(page);

  const card = klineCard(page);
  await expect(card, "K 线卡本身应仍在（失败是块内错误态，不是整块消失）").toBeVisible();

  const errorBlock = card.getByTestId("kline-error");
  await expect(errorBlock, "500 时 K 线块应显示错误态").toBeVisible();
  await expect(errorBlock).toContainText("加载失败");
  await expect(card.getByTestId("kline-retry")).toContainText(/重\s*试/);

  // 判别性断言：失败**不得**被写成"这只票没有 K 线"
  await expect(card, "K 线失败被伪装成「暂无K线数据」").not.toContainText("暂无");
  await expect(card.getByTestId("kline-ribbon"), "失败态不该有数据条").toHaveCount(0);

  // 同一屏的矛盾：报价条（同一查询键）已经在报「加载失败」，K 线卡不能同时说"没有数据"
  await expect(
    page.getByTestId("quote-position-strip"),
    "报价条与 K 线共用同一份日线，两处的失败/无记录判断必须一致",
  ).toContainText("加载失败");
});

test("K 线恢复端点后点「重试」：错误块消失、图真的回来", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await page.route(DAILY_GLOB, (route) => route.fulfill({ status: 500, body: "boom" }));

  await openDetail(page);
  const card = klineCard(page);
  const errorBlock = card.getByTestId("kline-error");
  await expect(errorBlock).toBeVisible();

  // 换回真后端再点重试：证明重试是真的重取，不是只把错误块藏起来
  await page.unroute(DAILY_GLOB);
  await card.getByTestId("kline-retry").click();

  await expect(errorBlock, "重试成功后不应再有错误块").toBeHidden();
  // 宽屏下数据条只在**有** bar 时渲染（canvas 里的内容断言不了，ribbon 是可读的等价证据）
  await expect(card.getByTestId("kline-ribbon"), "重试后仍没有 K 线，说明没有真的重取").toBeVisible();
  await expect(card).not.toContainText("暂无");
});
