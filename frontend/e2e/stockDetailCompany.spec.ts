import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * P2-3 E2E：公司概况（F17）+ 股性事实行（D2）。
 *
 * 四条用例，各钉一个可被机械否证的事实：
 *
 * 1. **600519 的公司概况真的显示了后端既有的四个字段**（全称 / 上市日期 / 地区 / 实控人）。
 *    F17 的根因是前端连类型都没映射，所以这里断言的是**具体值**（全称含「茅台」、
 *    上市日期是 2001 年），不是"卡在不在"——只断言卡片存在的话，四个 `--` 也能过。
 * 2. **股性事实行渲染**（`近60日` 等六个 chip + 一句人话）。
 * 3. **零新增请求**：`/quotes/daily` 恰好 1 次。股性行复用 K 线在 qfq 下的缓存键
 *    （`useCharacterStats`），键漂移一个字符就会变成 2 次 —— 本仓库最容易静默复发的缺陷。
 * 4. **NULL 字段整项不渲染**（设计稿「缺失 ≠ 0」）：把 enriched 响应里的四个字段改成
 *    `null` 后，公司概况整块**不出现**（不是一张写着四个 `--` 的卡）；只 null 掉两个时，
 *    只有那两个字段的行消失，另外两个照常。
 *
 * 断言纪律（与 stockDetailLayout.spec.ts / stockDetailRequests.spec.ts 同）：
 * `page.on("request")` 必须在 `goto` **之前**注册，否则首个请求漏记；锚点用页面上真实
 * 出现的元素，不用固定 sleep。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const STOCK = "/stock/600519";
const WIDE = { width: 1440, height: 900 };

/** 详情页的日线请求；`quotes/daily` 是 K 线、52 周统计、股性事实行**共用**的那一个端点。 */
function recordDailyRequests(page: Page): string[] {
  const urls: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("/quotes/daily")) urls.push(request.url());
  });
  return urls;
}

/** 公司概况卡片（`data-testid` 由 `CompanyProfile` 写在 AntD `Card` 上）。 */
function companyCard(page: Page) {
  return page.getByTestId("company-profile");
}

/** 打开详情页并等到报价条渲染（名录请求已返回）。 */
async function openDetail(page: Page) {
  await page.setViewportSize(WIDE);
  await page.goto(STOCK);
  await expect(page.getByTestId("quote-bar")).toBeVisible();
}

test("600519 公司概况：全称 / 上市日期 / 地区 / 实控人四个字段都上屏", async ({ page }) => {
  await openDetail(page);

  const card = companyCard(page);
  await expect(card).toBeVisible();
  await expect(card).toContainText("公司概况");

  // 四个 label 都在（顺序即 `CompanyProfile.profileRows` 的顺序，但不硬绑 DOM 顺序）
  for (const label of ["全称", "上市日期", "地区", "实控人"]) {
    await expect(card.getByText(label, { exact: true })).toBeVisible();
  }

  // 具体值：全称是后端 `full_name`（贵州茅台酒股份有限公司），不是名字的复制品
  const fullName = card.locator('[data-field="全称"] .company-profile__value');
  await expect(fullName).toContainText("茅台");
  await expect(fullName).toContainText("股份有限公司");

  // 上市日期：2001 年（带年份 —— 只写"8月27日"读者无从判断是哪一年）
  const listDate = card.locator('[data-field="上市日期"] .company-profile__value');
  await expect(listDate).toContainText(/^\d{4}年\d{1,2}月\d{1,2}日$/);
  await expect(listDate).toContainText("2001年");

  // 地区 / 实控人至少非空
  await expect(card.locator('[data-field="地区"] .company-profile__value')).not.toBeEmpty();
  await expect(card.locator('[data-field="实控人"] .company-profile__value')).not.toBeEmpty();

  // 有值的字段**不许**退化成 `--` 占位（缺失才不渲染，而不是画占位符）
  await expect(card).not.toContainText("--");
});

test("股性事实行：600519 渲染近60日等六个 chip + 一句人话", async ({ page }) => {
  await openDetail(page);

  const facts = page.getByTestId("character-facts");
  await expect(facts).toBeVisible();

  // 窗口标签逐字是「近60日」（brief 第 7 条的示例文案）
  await expect(facts).toContainText(/近60日\s*[+-]?\d+\.\d%/);
  // 其余五项：
  await expect(facts).toContainText(/年化波动\s*\d+\.\d%/);
  await expect(facts).toContainText(/最大回撤\s*-?\d+\.\d%/);
  await expect(facts).toContainText(/趋势 R²\s*\d\.\d{2}/);
  await expect(facts).toContainText(/均线(多头|未成多头)/);
  await expect(facts).toContainText(/量能\s*\d+\.\d×/);

  // 六个 chip 全部有非空的值（防止某一项渲染成空 span 也算"行在"）
  const chipValues = facts.locator(".character-facts__value");
  await expect(chipValues).toHaveCount(6);
  for (let i = 0; i < 6; i += 1) {
    await expect(chipValues.nth(i)).not.toBeEmpty();
  }

  // 一句人话 + 窗口截至（不是只有数字的"仪表盘"）
  await expect(facts.locator(".character-facts__reading")).toContainText("。");
  await expect(facts).toContainText(/窗口截至\s*\d{1,2}月\d{1,2}日/);
});

test("零新增请求：股性行渲染后 /quotes/daily 仍然恰好 1 次", async ({ page }) => {
  // ⚠️ 监听必须在 goto 之前：K 线 / 52 周统计 / 股性行 在页面挂载时立刻发请求
  const dailyRequests = recordDailyRequests(page);

  await openDetail(page);
  // 三个消费者都真的渲染了 = 三者都读到了数据（不是"没渲染所以没请求"）
  await expect(page.locator(".ant-card").filter({ hasText: "历史行情" })).toBeVisible();
  await expect(page.getByTestId("range-ribbon")).toBeVisible();
  await expect(page.getByTestId("character-facts")).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(
    dailyRequests,
    `概览页对 /quotes/daily 发起了 ${dailyRequests.length} 次请求（应为 1 次）：\n` +
      `${dailyRequests.join("\n")}\n` +
      "R7：股性事实行必须复用 K 线在 qfq 下的查询键 [\"kline\", `stock-kline-<symbol>`, \"qfq\"]" +
      "（与 useStockRangeStats 逐字相同），键不同就会多拉一份 3650 天日线。",
  ).toHaveLength(1);
});

test("公司概况四个字段全为 null → 整块不渲染（不是四个 `--` 的空卡）", async ({ page }) => {
  // 用真实的 enriched 响应做底，只把公司概况那四个字段改成 null ——
  // 其余字段与线上完全一致，排除"页面整体挂了所以卡片不在"的假绿。
  await page.route("**/stocks/600519/enriched*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.full_name = null;
    body.list_date = null;
    body.area = null;
    body.act_name = null;
    await route.fulfill({ response, json: body });
  });

  await openDetail(page);

  // 页面确实起来了（报价条在）——否则下面两条恒真
  await expect(page.getByTestId("quote-bar")).toBeVisible();
  // 整块不存在：testid 与标题都不在
  await expect(companyCard(page)).toHaveCount(0);
  await expect(page.getByText("公司概况", { exact: true })).toHaveCount(0);
  // 也没有把缺失渲染成占位符（`--` 是"有这一项但没值"的形态，这里连项都不该有）
  await expect(page.locator("body")).not.toContainText("实控人");
});

test("公司概况只有部分字段为 null → 只少那两行，另外两行照常", async ({ page }) => {
  await page.route("**/stocks/600519/enriched*", async (route) => {
    const response = await route.fetch();
    const body = await response.json();
    body.area = null;
    body.act_name = null;
    await route.fulfill({ response, json: body });
  });

  await openDetail(page);

  const card = companyCard(page);
  // 还有两行有值 ⇒ 卡片仍在
  await expect(card).toBeVisible();
  await expect(card.locator('[data-field="全称"]')).toBeVisible();
  await expect(card.locator('[data-field="上市日期"]')).toBeVisible();
  // 被 null 掉的两行**整行都不存在**（不含 label、不含占位符、不留空行）
  await expect(card.locator('[data-field="地区"]')).toHaveCount(0);
  await expect(card.locator('[data-field="实控人"]')).toHaveCount(0);
  await expect(card).not.toContainText("地区");
  await expect(card).not.toContainText("实控人");
  await expect(card).not.toContainText("--");
});

test("成交量全为 0 → 量能那一格整项不出现，人话也不提量能（不渲染 0.0×）", async ({ page }) => {
  /**
   * review round 1 的回归用例：`volumeRatio` 曾经在 `0/0` 时返回 `0`，于是零成交的窗口
   * 会印出「量能 0.0×」，人话还写「最近 5 日量能萎缩」—— 用两个断言把缺失说成了事实。
   *
   * 造数：把日线端点整段换成 60 根"收盘价每天 +1、成交量恒 0"的 bar。
   * 60 根满窗 ⇒ 其余五项照常算得出来（这条同时证明"没有把整行藏掉"）。
   */
  const dates = Array.from({ length: 60 }, (_, i) => {
    const day = new Date(Date.UTC(2026, 0, 1 + i));
    return day.toISOString().slice(0, 10);
  });
  const bars = dates.map((trade_date, i) => ({
    trade_date,
    open: 100 + i,
    high: 100 + i,
    low: 100 + i,
    close: 100 + i,
    volume: 0,
    amount: 0,
  }));

  await page.route("**/quotes/daily*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        symbol: "600519",
        name: "贵州茅台",
        exchange: "Shanghai_Stocks",
        data: bars,
        adjust_available: true,
      }),
    })
  );

  await openDetail(page);

  const facts = page.getByTestId("character-facts");
  await expect(facts).toBeVisible();

  // 其余五项仍在（不是"整行不渲染"）
  await expect(facts).toContainText(/近60日\s*[+-]?\d+\.\d%/);
  await expect(facts).toContainText(/年化波动/);
  await expect(facts).toContainText(/最大回撤/);
  await expect(facts).toContainText(/趋势 R²/);
  await expect(facts).toContainText(/均线/);

  // 量能这一格**整项缺席**：既没有 0.0×，也没有 `--` 占位
  await expect(facts.locator('[data-fact="volumeRatio"]')).toHaveCount(0);
  await expect(facts).not.toContainText("量能");
  await expect(facts).not.toContainText("0.0×");
  // 人话里也不许出现量能那句话（"萎缩/放大/相当"都是对一个不存在的量的断言）
  await expect(facts.locator(".character-facts__reading")).not.toContainText(/量能|放量|缩量/);

  // chip 数从 6 降到 5，且没有空值格
  await expect(facts.locator(".character-facts__value")).toHaveCount(5);
});
