import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * P1-3 E2E：概览重排的四条硬约束（F13 URL tab / F14 栅格与标签行 / F15 报价条）。
 *
 * 每条用例只钉一个可被机械否证的事实：
 *
 * 1. **深链直达**：`/stock/600519?tab=financial` 首屏就是财务面板，不需要点。
 *    这条同时钉住「旧路径 redirect 不能丢 query」——`/stock/:symbol` → `/company/:symbol`
 *    的跳转若丢掉 `?tab=`，F13 在入口处就失效（页面自己救不回来）。
 * 2. **切换写 URL**：点「估值」后地址栏出现 `?tab=valuation`，刷新保持。
 * 3. **报价条八项**：今开 最高 最低 昨收 成交量 成交额 换手率 总市值 同时可见；
 *    1440×900 下整条高度 ≤ 96px（设计稿 §2 的行高预算）。
 * 4. **区间条**：渲染时 `aria-label` 同时含「区间」与「距高点」（读屏与几何等价）。
 * 5. **390×844**：无横向溢出 + 标签区 ≤2 行（F14 的移动端判据）。
 * 6. **零新增请求（R7）**：`/quotes/daily` 恰好 1 次 —— 52 周统计复用 K 线的 qfq 缓存键，
 *    键漂移一个字符就会变成 2 次（本仓库最容易静默复发的缺陷类型）。
 *
 * 断言纪律（与 stockDetailRequests.spec.ts / stockDetailFinancial.spec.ts 同）：
 * `page.on("request")` 必须在 `goto` **之前**注册，否则首个请求漏记；
 * 锚点用页面上真实出现的元素，不用固定 sleep。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const STOCK = "/stock/600519";
const WIDE = { width: 1440, height: 900 };
const NARROW = { width: 390, height: 844 };

/** 行 3 的八个标签（逐字，顺序即设计稿 §2 的顺序）。 */
const METRIC_LABELS = ["今开", "最高", "最低", "昨收", "成交量", "成交额", "换手率", "总市值"];

/** 详情页的日线请求；`quotes/daily` 是 K 线与 52 周统计**共用**的那一个端点。 */
function recordDailyRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/quotes/daily")) urls.push(request.url());
  });
  return urls;
}

/** 打开详情页并等到报价条真的渲染出来（名录请求已返回）。 */
async function openQuoteBar(page: Page) {
  await page.goto(STOCK);
  const bar = page.getByTestId("quote-bar");
  await expect(bar).toBeVisible();
  return bar;
}

test("深链 ?tab=financial：首屏直达财务面板（旧路径 redirect 不丢 query）", async ({ page }) => {
  await page.goto(`${STOCK}?tab=financial`);

  // 财务 tab 处于选中态，且**没有点击**过任何东西
  const financialTab = page.getByRole("tab", { name: "财务", exact: true });
  await expect(financialTab).toHaveAttribute("aria-selected", "true");

  // 面板内容真的挂上了（不只是 tab 高亮）：财务 tab 自己的卡片标题可作为锚点
  await expect(page.locator(".ant-tabs-tabpane-active")).toContainText("财务摘要");

  // redirect 后 query 仍在（`/company/600519?tab=financial`）
  await expect(page).toHaveURL(/[?&]tab=financial/);
});

test("切换「估值」写入 URL，刷新后保持", async ({ page }) => {
  await openQuoteBar(page);

  await page.getByRole("tab", { name: "估值", exact: true }).click();
  await expect(page).toHaveURL(/[?&]tab=valuation/);
  await expect(page.locator(".ant-tabs-tabpane-active")).toContainText("估值摘要");

  await page.reload();
  await expect(page.getByRole("tab", { name: "估值", exact: true })).toHaveAttribute(
    "aria-selected",
    "true",
  );
  await expect(page).toHaveURL(/[?&]tab=valuation/);
  await expect(page.locator(".ant-tabs-tabpane-active")).toContainText("估值摘要");
});

test("报价条：八项标签同时可见，1440 下整条 ≤96px", async ({ page }) => {
  await page.setViewportSize(WIDE);
  const bar = await openQuoteBar(page);

  for (const label of METRIC_LABELS) {
    // `data-metric` 是报价条写出的语义锚点（不依赖 AntD 内部类名）
    await expect(bar.locator(`[data-metric="${label}"]`)).toBeVisible();
  }

  const box = await bar.boundingBox();
  expect(box, "报价条未渲染出可测量的盒子").not.toBeNull();
  expect(box!.height, `报价条高度 ${box!.height}px 超出设计稿 96px 预算`).toBeLessThanOrEqual(96);

  // 「数据截至」必须在（它绑最新行情 trade_date，不是名录 asof）
  await expect(page.getByTestId("quote-asof")).toContainText(/数据截至/);
});

test("52 周区间条：渲染时 aria-label 同时含「区间」与「距高点」", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await openQuoteBar(page);

  const ribbon = page.getByTestId("range-ribbon");
  // 区间条由 K 线缓存算出：等它出现（600519 有 250 根以上样本时必然渲染）
  await expect(ribbon).toBeVisible();
  const label = await ribbon.getAttribute("aria-label");
  expect(label ?? "", "区间条的 aria-label 缺少「区间」").toContain("区间");
  expect(label ?? "", "区间条的 aria-label 缺少「距高点」").toContain("距高点");
  expect(label ?? "", "区间条的 aria-label 缺少「距低点」").toContain("距低点");
});

test("390×844：无横向溢出，标签区 ≤2 行", async ({ page }) => {
  await page.setViewportSize(NARROW);
  await page.goto(STOCK);
  await expect(page.getByTestId("stock-tag-row")).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  const overflow = await page.evaluate(() => {
    const el = document.scrollingElement ?? document.documentElement;
    return el.scrollWidth - el.clientWidth;
  });
  expect(overflow, `390px 下横向溢出 ${overflow}px（右侧内容被推出屏外）`).toBeLessThanOrEqual(0);

  // 「标签区行数」= 芯片盒子出现的不同 `top` 值个数（换行必然改变 top）。
  // 用几何事实而不是"数 DOM"：标签在同一行时 top 相同，换行即多一档。
  const rows = await page
    .getByTestId("stock-tag-row")
    .locator(".ant-tag")
    .evaluateAll((els) =>
      [...new Set(els.map((el) => Math.round(el.getBoundingClientRect().top)))].length,
    );
  expect(rows, `390px 下标签区占 ${rows} 行（判据 ≤2 行）`).toBeLessThanOrEqual(2);
});

test("零重复请求：/quotes/daily 恰好 1 次（R7 缓存键复用）", async ({ page }) => {
  // ⚠️ 监听必须在 goto 之前：K 线与 52 周统计在页面挂载时立刻发请求
  const dailyRequests = recordDailyRequests(page);

  await page.setViewportSize(WIDE);
  await page.goto(STOCK);
  // 锚点：K 线卡与区间条都已渲染 = 日线数据已到、两处消费者都已挂载
  await expect(page.locator(".ant-card").filter({ hasText: "历史行情" })).toBeVisible();
  await expect(page.getByTestId("range-ribbon")).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(
    dailyRequests,
    `概览页对 /quotes/daily 发起了 ${dailyRequests.length} 次请求（应为 1 次）：\n` +
      `${dailyRequests.join("\n")}\n` +
      "R7：52 周统计必须复用 K 线在 qfq 下的查询键 [\"kline\", `stock-kline-<symbol>`, \"qfq\"]，" +
      "键不同就会多拉一份 3650 天日线。",
  ).toHaveLength(1);
});

test("估值分位与估值 tab 逐字一致（同一份缓存，不是另一个口径）", async ({ page }) => {
  await page.setViewportSize(WIDE);
  await openQuoteBar(page);

  // 分位来自独立查询：`--` 是"还没到/无样本"，必须等它变成数字再比（否则比的是两个 `--`）
  const strip = page.getByTestId("quote-position-strip");
  await expect(strip).toContainText(/PE_TTM 3年分位\s*[\d.]+%/);
  const stripPct = (await strip.innerText()).match(/PE_TTM 3年分位\s*([\d.]+%)/)?.[1];
  expect(stripPct, `报价条没有渲染出 PE_TTM 3年分位：${await strip.innerText()}`).toBeTruthy();

  await page.getByRole("tab", { name: "估值", exact: true }).click();
  const cell = page
    .locator(".ant-card")
    .filter({ hasText: "估值摘要" })
    .locator(".ant-col")
    .filter({ hasText: "PE(TTM) 3年分位" })
    .first();
  await expect(cell).toBeVisible();
  await expect(cell).toContainText(/[\d.]+%/);
  const tabPct = (await cell.innerText()).match(/([\d.]+%)/)?.[1];

  expect(
    tabPct,
    `报价条分位 ${stripPct} 与估值 tab 分位 ${tabPct} 不一致 —— ` +
      "两处必须复用同一个 query key（valuation-history / pe_ttm / 3y）",
  ).toBe(stripPct);
});

test("取数失败：行 4 出现「重试」，不退化成「无数据」形态", async ({ page }) => {
  await page.setViewportSize(WIDE);
  // 只打瘫日线端点：52 周统计必然失败，其余端点照常（模拟"这一块坏了"而不是"整页坏了"）
  await page.route("**/quotes/daily*", (route) => route.fulfill({ status: 500, body: "boom" }));

  await page.goto(STOCK);
  const strip = page.getByTestId("quote-position-strip");
  await expect(strip).toBeVisible();

  // 设计稿 §3：失败态是行末一枚「加载失败 · 重试」，**不是**空态文案
  const retry = strip.getByRole("button", { name: /重试/ });
  await expect(retry).toBeVisible();
  await expect(retry).toContainText("加载失败");
  await expect(strip, "失败被伪装成「无数据/暂无」").not.toContainText(/暂无|无数据/);

  // 「数据截至」不依赖这两个查询，失败时仍在
  await expect(page.getByTestId("quote-asof")).toContainText("数据截至");

  // 恢复后点重试 → 区间条真的出现（证明重试是真的重取，不是只把按钮藏了）
  await page.unroute("**/quotes/daily*");
  await retry.click();
  await expect(page.getByTestId("range-ribbon")).toBeVisible();
});
