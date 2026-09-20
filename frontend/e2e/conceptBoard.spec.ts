import { expect, test } from "@playwright/test";

/**
 * 概念板块 E2E（契约 §2.3 / §3 触点 B/C/D/E）：
 * - 「成分截至 X（东财）」必须用 `membership_as_of` 渲染（历史口径声明是契约要求，不是装饰）；
 * - 缺失 ≠ 0：无行情成分的涨跌幅/换手/市值渲染 `--`，不得渲染 0/0.00%；
 * - 板内梯队来自 `/concepts/{code}` 的 echelons（与连板梯队卡同形）；
 * - 连板列同样由 echelons 拍平：不在梯队的成分渲染 `--`（缺失 ≠ 0 板）；
 * - 追加列必须排在 `fixed: "right"` 的自选列**之前**（自选列是右缘 sticky 槽位，必须最后一列）；
 * - 降级/404 各自空态；概念标签失败零占位；次新股卡 KPI 与 items 同源、替代口径上屏。
 *
 * 载荷自洽性：DETAIL 的 `as_of` 与 `membership_as_of` **刻意取不同值**（09-18 vs 09-17），
 * 页面若误把 `as_of` 当成分口径，本文件的 `not.toContainText("9月18日")` 会红。
 *
 * 说明：T16 brief 的同一段断言里还有 `getByText("未开板")`，此处**刻意不含**：
 * `/concepts/{code}` 的响应（§2.2 ConceptKpisOut）没有未开板字段，mock 载荷里也没有，
 * 页面无从渲染；「未开板」属于次新股情绪卡（T15，见本文件后两个用例断言）。
 * 在这里断言它只会得到一个假绿（或假红），不测任何真实行为。
 */
const DETAIL = {
  as_of: "2026-09-18", membership_as_of: "2026-09-17", source: "em_clist", degraded_reason: null,
  board: { board_code: "BK0501", board_name: "次新股", member_count: 165, unresolved_count: 0,
    priced_count: 165, up_count: 157, flat_count: 3, down_count: 5, avg_pct: 4.66,
    main_net_inflow: 1234567890.0, main_net_ratio: 2.1, lead_stock_name: "某股",
    lead_stock_code: "601091", lead_stock_pct: 20.0,
    leaders: [{ symbol: "601091", name: "C沈鼓", change_percent: 20.0 }] },
  kpis: { zt_count: 9, max_streak: 3, leader_symbol: "601091", leader_name: "C沈鼓" },
  echelons: [{ streak: 3, label: "3连板", stocks: [{ symbol: "601091", name: "C沈鼓", streak: 3,
    days_span: 3, boards_in_window: 3, missing_days: 0, amount: 1.0e8,
    seal_time: null, seal_fund: null, break_count: null }] }],
  unresolved_count: 0, stock_count: 165,
};
const STOCKS = [
  { symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks", category: "stock",
    list_date: "2026-09-17", asof: "2026-05-08T00:00:00Z", latest_price: 20.8,
    change_percent: 20.0, turnover_rate: 89.08, circ_mv: 179664849567.0, amount: 2181187.373,
    latest_quote_date: "2026-09-18" },
  // 缺失值样本：无行情 → 涨跌幅/换手渲染 `--`，不得渲染 0
  { symbol: "688837", name: "C信诺维", exchange: "Shanghai_Stocks", category: "stock",
    list_date: null, asof: "2026-05-08T00:00:00Z", latest_price: null, change_percent: null,
    turnover_rate: null, circ_mv: null, amount: null, latest_quote_date: null },
  // 排序样本：源顺序最后一个、涨跌幅最高且不在梯队 —— 默认降序若没生效，它不会排首位
  // （否则"首行是 C沈鼓"与源顺序同形，断言假绿）；也不在 echelons 里 → 连板列必须 `--`
  { symbol: "301234", name: "C复核", exchange: "Shenzen_Stocks", category: "stock",
    list_date: "2026-09-16", asof: "2026-05-08T00:00:00Z", latest_price: 33.5,
    change_percent: 33.5, turnover_rate: 41.2, circ_mv: 8.0e9, amount: 3.3e6,
    latest_quote_date: "2026-09-18" },
];

/** 两条 mock 路由：详情在前、成分（更具体的路径）在后注册，注册顺序不影响最终匹配结果。
 * 注意：本文件任何注释都不得写「星号星号斜杠」的 glob —— 它会提前闭合块注释（踩过）。
 */
async function routeConceptDetail(page: import("@playwright/test").Page, detail: unknown, stocks: unknown) {
  await page.route("**/api/v1/concepts/BK0501", (r) => r.fulfill({ json: detail }));
  await page.route("**/api/v1/concepts/BK0501/stocks", (r) => r.fulfill({ json: stocks }));
}

test("概念详情页展示成分与板内梯队，并宣布成分口径", async ({ page }) => {
  await routeConceptDetail(page, DETAIL, STOCKS);
  await page.goto("/market/concept/BK0501");
  await expect(page.getByTestId("concept-board")).toBeVisible();
  await expect(page.getByRole("heading", { name: "次新股" })).toBeVisible();
  // 历史口径声明必须在页面上，且必须读 `membership_as_of`（09-17）而不是 `as_of`（09-18）：
  // 两个值刻意不同，认错字段即红。分母 n 同时钉住 priced 口径 = up+flat+down（157+3+5）。
  const asofLine = page.getByText(/成分截至/);
  await expect(asofLine).toContainText("成分截至 9月17日（东财）");
  await expect(asofLine).not.toContainText("9月18日");
  await expect(asofLine).toContainText("n=165 有行情家数");
  // KPI 瓦片（同一响应的 items 口径，不新造指标）
  await expect(page.getByText("板内涨停")).toBeVisible();
  await expect(page.getByText("12.35 亿")).toBeVisible();          // 元 → 亿
  await expect(page.getByText("95.2%")).toBeVisible();             // 157 / (157+3+5)；flat≠0 才有意义
  // 板内梯队（复用 <LimitUpLadder>）
  const ladder = page.locator(".sentiment-ladder");
  await expect(ladder).toContainText("3连板");
  await expect(ladder).toContainText("C沈鼓");
  // 成分表：命中票渲染真实值；无行情票一律 `--`，不得渲染 0
  await expect(page.locator("tr", { hasText: "C沈鼓" })).toContainText("+20.00%");
  // Q1（评审修复）：追加的连板列必须排在 `fixed: "right"` 的自选列之前 ——
  // 排到 pin 之后时连板列会顶到右缘、与 pin 的 sticky 槽位（right:0 那一段）重叠，
  // 而排错时连板列恰好就是最后一列 —— 旧的 `td:last` 断言取到的仍是同一个值，永远绿。
  // 故按表头文案定位列号，并钉住「连板列不是最后一列」。
  const heads = (await page.locator(".ant-table-thead th").allInnerTexts()).map((t) => t.trim());
  const streakCol = heads.indexOf("连板");
  expect(streakCol).toBeGreaterThan(-1);
  expect(streakCol).toBeLessThan(heads.length - 1);   // 连板列不得是最后一列（最后一列是固定自选列）
  // 连板列（§3 触点 B）：echelons 拍平；命中票 `3板`，不在梯队的成分 `--`（缺失 ≠ 0 板）
  const hitRow = page.locator("tr", { hasText: "C沈鼓" });
  await expect(hitRow.locator("td").nth(streakCol)).toHaveText("3板");
  const missingRow = page.locator("tr", { hasText: "C信诺维" });
  await expect(missingRow).toContainText("--");
  await expect(missingRow).not.toContainText("0.00%");
  // 排序接线：默认涨跌幅降序 → 源顺序末位的 +33.50% 排首位（行序与表头箭头同源，不是死箭头）
  await expect(page.locator(".ant-table-tbody tr.ant-table-row").first()).toContainText("C复核");
  // 不在梯队的成分连板列 `--`（缺失 ≠ 0 板）
  const offLadderRow = page.locator("tr", { hasText: "C复核" });
  await expect(offLadderRow.locator("td").nth(streakCol)).toHaveText("--");
});

test("概念详情降级：横幅写明原因，未收录成分单独告警，梯队不显示空态", async ({ page }) => {
  // `no_members` + 未收录成分同时出现：两条文案必须各自上屏，不得互相吞掉
  await routeConceptDetail(
    page,
    { ...DETAIL, degraded_reason: "no_members", unresolved_count: 7, echelons: [] },
    STOCKS,
  );
  await page.goto("/market/concept/BK0501");
  await expect(page.getByText("成分数据尚未采集（每日 18:20 刷新）")).toBeVisible();
  await expect(page.getByTestId("concept-unresolved")).toContainText("另有 7 只成分股未收录");
  // 降级时梯队走组件占位，不得显示「今日板内无涨停」（那是真空态的文案，会把降级读成无涨停）
  await expect(page.locator(".sentiment-ladder")).toContainText("数据不完整，暂不展示梯队");
  await expect(page.getByText("今日板内无涨停")).toHaveCount(0);
});

test("概念详情 404：空态 + 回跳链接，且 4xx 不重试（只请求一次）", async ({ page }) => {
  let hits = 0;
  await page.route("**/api/v1/concepts/BK9999", (r) => {
    hits += 1;
    return r.fulfill({ status: 404, json: { code: "NOT_FOUND", message: "概念板块不存在" } });
  });
  await page.goto("/market/concept/BK9999");
  await expect(page.getByText("未找到该概念板块")).toBeVisible();
  await expect(page.getByRole("link", { name: "返回概念板块列表" })).toBeVisible();
  // 404 是终态：`retryExcept4xx` 必须拦住重试（重试会让用户多等约 7s 才看到空态）
  await page.waitForTimeout(1_500);
  expect(hits).toBe(1);
});

/**
 * 概念**列表页**（`/market/hot-sectors/concept`）的口径载荷：12 个板块，刻意多于东财路径的
 * Top-10（`_hot_board_items_from_eastmoney` 切片上限）。
 *
 * 五处刻意设下的反例：
 * - `as_of`(09-18) 与 `membership_as_of`(09-17) **不同**：把行情判据日误当成分快照日会被日期断言抓住；
 * - `BK9012` 的 `avg_pct: null` + 领涨股 `change_percent: null`：缺失必须渲染 `--`，绝不能 0 填充
 *   成 `0.00%`（无行情成分进 tag 还会让 `toFixed` 崩）；
 * - `BK9001.flat_count = 3`：平盘家数 > 0 才有意义（缺失 ≠ 0 的正样本）；
 * - `BK9011.avg_pct = -3`（负样本）：`null` 必须排在**下跌板块之后**。全正样本时「null 排最后」
 *   与「null 当 0 排」同形（假绿），有了负样本，`?? 0` 会把 -3 挤到 null 之后 → 行序断言红；
 * - `total: 504` ≠ `items.length`(12)：分页器必须读信封 `total`；读 `rows.length` 时只剩 2 页。
 */
const CONCEPT_BOARDS = {
  as_of: "2026-09-18",
  membership_as_of: "2026-09-17",
  price_source: "local_agg",
  flow_source: "em_clist",
  total: 504, // 全库启用板块数（刻意 ≠ items.length = 12）
  degraded_reason: null,
  items: Array.from({ length: 12 }, (_, i) => {
    const n = i + 1;
    const nn = String(n).padStart(2, "0");
    return {
      board_code: `BK90${nn}`,
      board_name: `聚合概念${nn}`,
      member_count: 50 + n,
      unresolved_count: 0,
      priced_count: 50 + n,
      up_count: 30,
      flat_count: n === 1 ? 3 : 0,
      down_count: 20,
      avg_pct: n === 12 ? null : n === 11 ? -3 : 13 - n,
      main_net_inflow: n === 12 ? null : n * 1.0e8,
      main_net_ratio: n === 12 ? null : 1.5,
      lead_stock_name: n === 12 ? null : `领涨${nn}`,
      lead_stock_code: n === 12 ? null : `6000${nn}`,
      lead_stock_pct: n === 12 ? null : 9.9,
      leaders:
        n === 12
          ? [{ symbol: "600012", name: "无行情领涨", change_percent: null }]
          : [{ symbol: `6000${nn}`, name: `领涨${nn}`, change_percent: 9.9 }],
    };
  }),
};

/**
 * 概念列表端点（带 query string）的路由正则：`**` glob 会连 `/concepts/{code}` 详情一起吞掉，
 * 用 `(\?|$)` 精确钉住「列表（有 query）/ 裸列表」两种形态，详情不被误 mock。
 */
const CONCEPT_LIST_ROUTE = /\/api\/v1\/concepts(\?|$)/;

test("概念列表走本地聚合全量：口径披露 + 全量分页 + 缺行情渲染 `--` + 行点击进详情", async ({ page }) => {
  const conceptRequests: string[] = [];
  let liveHits = 0;
  // 概念分类**不得**再打东财实时信封（`limit=1000` 的本地聚合列表才是「查看全部」的落点）
  await page.route("**/api/v1/market/hot-boards**", (r) => {
    liveHits += 1;
    return r.fulfill({
      json: {
        as_of: null,
        as_of_quality: "partial",
        as_of_reason: null,
        source: "eastmoney_boards",
        degraded_reason: null,
        items: [],
      },
    });
  });
  await page.route(CONCEPT_LIST_ROUTE, (r) => {
    conceptRequests.push(r.request().url());
    return r.fulfill({ json: CONCEPT_BOARDS });
  });

  await page.goto("/market/hot-sectors/concept");

  // (a) 口径披露：行情判据日与**成分**快照日是两个口径，必须各自对位（认错字段即红）
  const caption = page.getByText(/按本地成分聚合/);
  await expect(caption).toBeVisible();
  await expect(caption).toContainText("行情截至 9月18日");
  // 列表页的 `membership_as_of` 是**全库**成分表最大快照日（可能新于某板块自身快照），
  // 故措辞是「成分快照 X（全库 · 东财）」，与详情页逐板口径的「成分截至 X（东财）」区分。
  await expect(caption).toContainText("成分快照 9月17日（全库 · 东财）");

  // (b) 全量 > 东财 Top-10：分页器必须出现第 2 页；东财路径 10 行时不会有 `.ant-pagination-item-2`
  const pager = page.locator(".ant-pagination");
  await expect(pager.locator(".ant-pagination-item-2")).toBeVisible();
  // (b2) 分页总数必须读**信封** `total`(504)，不是 `items.length`(12)：读 rows.length 时只有
  //      2 页，第 5 页与跳页器都不存在 → 本断言红（这是「查看全部」兑现与否的判据）。
  await expect(pager.locator(".ant-pagination-item-5")).toBeVisible();
  await expect(pager.locator(".ant-pagination-jump-next")).toBeVisible();

  // (c) 概念路径打的确实是我们的列表端点，且带 `limit=1000`（东财 hot-boards 永远没有这个参数）
  expect(conceptRequests.length).toBeGreaterThan(0);
  expect(conceptRequests.every((u) => new URL(u).searchParams.get("limit") === "1000")).toBe(true);
  expect(liveHits).toBe(0);

  // 列号按表头文案现取：概念分类隐藏了成交额列（见 (f)），写死 nth 会把断言指到相邻列上。
  const columnIndex = async (title: string) => {
    const heads = (await page.locator(".ant-table-thead th").allInnerTexts()).map((t) => t.trim());
    const index = heads.indexOf(title);
    expect(index, `表头「${title}」必须存在`).toBeGreaterThan(-1);
    return index;
  };
  const pctColumn = await columnIndex("板块涨跌幅");

  // (e) `avg_pct: null` 必须排在**下跌板块之后**：第 2 页只有 -3.00%（聚合概念11）与 null
  //     （聚合概念12）两行，行序即判据。全正样本时「null 排最后」与「null 当 0 排」同形，
  //     负样本 + 顺序断言才可证伪：`?? 0` 会把 null 插到 -3 之前 → 本断言红。
  await pager.locator(".ant-pagination-item-2").click();
  const pageTwoRows = page.locator(".ant-table-tbody tr.ant-table-row:not(.ant-table-measure-row)");
  await expect(pageTwoRows).toHaveCount(2);
  await expect(pageTwoRows.locator("td:first-child strong")).toHaveText(["聚合概念11", "聚合概念12"]);
  await expect(pageTwoRows.first().locator("td").nth(pctColumn)).toHaveText("-3.00%");
  const nullRow = page.locator("tbody tr", { hasText: "聚合概念12" });
  await expect(nullRow).toBeVisible();
  await expect(nullRow.locator("td").nth(pctColumn)).toHaveText("--");
  await expect(nullRow).not.toContainText("0.00%");

  // (f) 概念侧没有成交额字段（恒 `--`）：整列隐藏，不留一个点了没反应的排序表头
  await expect(page.getByRole("columnheader", { name: "成交额" })).toHaveCount(0);

  // 平盘家数 > 0 的样本回到首页断言（列号同样按表头取）
  await pager.locator(".ant-pagination-item-1").click();
  const flatRow = page.locator("tbody tr", { hasText: "聚合概念01" });
  await expect(flatRow.locator("td").nth(await columnIndex("平盘家数"))).toHaveText("3");

  // (d) 行点击仍进概念详情页（既有行为不回退），且概念分类不打开下钻抽屉
  await flatRow.getByRole("cell", { name: /聚合概念01/ }).first().click();
  await expect(page).toHaveURL(/\/market\/concept\/BK9001$/);
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("概念列表降级（no_quotes）：横幅如实上屏，不冒充空数据也不冒充失败", async ({ page }) => {
  await page.route(CONCEPT_LIST_ROUTE, (r) =>
    r.fulfill({ json: { ...CONCEPT_BOARDS, degraded_reason: "no_quotes", items: [] } })
  );
  await page.goto("/market/hot-sectors/concept");
  // 全站 `degraded_reason` 词表的 `no_quotes` 文案必须落在页面上（删掉降级分支即红）
  await expect(page.getByText("库内暂无行情数据")).toBeVisible();
  // 降级 ≠ 请求失败：失败文案不得出现（两条分支不得互相吞掉）
  await expect(page.getByText(/概念数据加载失败/)).toHaveCount(0);
});

test("概念列表请求失败：错误文案上屏（不是空表、也不是降级）", async ({ page }) => {
  await page.route(CONCEPT_LIST_ROUTE, (r) => r.abort());
  await page.goto("/market/hot-sectors/concept");
  await expect(page.getByText("概念数据加载失败，稍后重试")).toBeVisible();
  await expect(page.getByText("库内暂无行情数据")).toHaveCount(0);
});

test("行业分类仍走东财实时信封（概念列表端点不得被行业/地域借用）", async ({ page }) => {
  let conceptHits = 0;
  await page.route(CONCEPT_LIST_ROUTE, (r) => {
    conceptHits += 1;
    return r.fulfill({ json: CONCEPT_BOARDS });
  });
  await page.route("**/api/v1/market/hot-boards**", (r) =>
    r.fulfill({
      json: {
        as_of: "2026-09-18",
        as_of_quality: "partial",
        as_of_reason: null,
        source: "eastmoney_boards",
        degraded_reason: null,
        items: [
          {
            id: "industry-BK1261",
            name: "种植业",
            code: "BK1261",
            changePercent: 2.5,
            upCount: 10,
            flatCount: 1,
            downCount: 2,
            mainNetInflow: 1.0e8,
            amount: 3.0e8,
            leaders: [{ symbol: "600011", name: "某股", changePercent: 5.0 }],
          },
        ],
      },
    })
  );
  await page.route("**/api/v1/market/boards/*/stocks*", (r) => r.fulfill({ json: [] }));

  await page.goto("/market/hot-sectors/industry");
  await expect(page.getByRole("row", { name: /种植业/ })).toBeVisible();
  // 成交额列只对概念分类隐藏：行业侧仍有东财成交额，列与排序表头必须保留
  await expect(page.getByRole("columnheader", { name: "成交额" })).toBeVisible();
  // 本地聚合口径披露是概念分类专属：行业页不得出现，也不得请求概念列表端点
  await expect(page.getByText(/按本地成分聚合/)).toHaveCount(0);
  expect(conceptHits).toBe(0);

  // 行业语义未变：点击仍开成分股抽屉（概念路径的改动只影响概念分类）
  await page.getByRole("row", { name: /种植业/ }).getByRole("cell", { name: /种植业/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByText("该板块暂无成分股数据")).toBeVisible();
});

/** 个股页用例共用的 enriched 载荷（申万映射为空 → 面包屑降级为「其他」，与本用例无关）。 */
const STOCK_600000 = {
  symbol: "600000", name: "浦发银行", exchange: "Shanghai_Stocks", category: "stock",
  asof: "2026-05-08T00:00:00Z", latest_price: 10.0, change_percent: 1.0,
  latest_quote_date: "2026-09-18",
};

/**
 * 触点 C（个股详情「所属概念」）与触点 D（「次新股情绪」卡）的 e2e。
 * 两者的关键契约都是**零占位**：概念标签失败/为空时整块消失（`所属概念` 文案与 testid 都必须是 0），
 * 次新股卡的 `--` 必须来自缺失（`never_broken === null` 不可判），不是 0。
 */
const NEW_STOCKS_DEGRADED_SINGLE = {
  as_of: "2026-09-18", membership_as_of: null, source: "em_clist",
  degraded_reason: null, board_code: "BK0501", board_name: "次新股", unresolved_count: 0,
  // kpis 与 items 同源（1 只：涨停、不可判、高于首日开盘）
  kpis: { up_count: 1, flat_count: 0, down_count: 0, unpriced_count: 0,
    limit_up_count: 1, unbroken_count: 0, above_first_open_count: 1, avg_pct: 20.0 },
  items: [{ symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks",
    list_date: "2026-09-17", listed_trade_days: 2, pct_chg: 20.0, close: 20.8,
    turnover_rate: 89.08, circ_mv: 179664849567.0, amount: 2181187.373,
    streak: 2, is_lu: true, never_broken: null, first_open: 13.0, above_first_open: true }],
};

/**
 * 触点 D 的混合载荷（T16 brief 用例 4）：3 只样本分别 `never_broken` = null / true / false，
 * `kpis` 逐项由 `items` 推得（1 只 `is_lu` ⇒ `limit_up_count: 1`），
 * `as_of` 与 `membership_as_of` 仍刻意不同（09-18 / 09-17）以钉住卡内成分口径。
 */
const NEW_STOCKS_MIXED = {
  as_of: "2026-09-18", membership_as_of: "2026-09-17", source: "em_clist",
  degraded_reason: null, board_code: "BK0501", board_name: "次新股", unresolved_count: 2,
  kpis: { up_count: 2, flat_count: 0, down_count: 1, unpriced_count: 0,
    limit_up_count: 1, unbroken_count: 1, above_first_open_count: 2, avg_pct: 7.17 },
  items: [
    { symbol: "601091", name: "C沈鼓", exchange: "Shanghai_Stocks", list_date: "2026-09-17",
      listed_trade_days: 2, pct_chg: 20.0, close: 20.8, turnover_rate: 89.08,
      circ_mv: 179664849567.0, amount: 2181187.373, streak: 2, is_lu: true,
      never_broken: null, first_open: 13.0, above_first_open: true },
    { symbol: "688837", name: "C信诺维", exchange: "Shanghai_Stocks", list_date: "2026-09-15",
      listed_trade_days: 4, pct_chg: 5.0, close: 31.2, turnover_rate: 22.5,
      circ_mv: 6.2e9, amount: 1.1e6, streak: null, is_lu: false,
      never_broken: true, first_open: 28.0, above_first_open: true },
    { symbol: "301234", name: "C复核", exchange: "Shenzen_Stocks", list_date: "2026-09-10",
      listed_trade_days: 7, pct_chg: -3.5, close: 18.4, turnover_rate: 12.1,
      circ_mv: 4.4e9, amount: 9.0e5, streak: null, is_lu: false,
      never_broken: false, first_open: 19.5, above_first_open: false },
  ],
};

test("个股页渲染所属概念标签：缺失涨跌幅只显示名字，点击进入概念详情", async ({ page }) => {
  await page.route("**/api/v1/exchanges/Shanghai_Stocks/stocks/600000/enriched", (r) =>
    r.fulfill({ json: STOCK_600000 }));
  await page.route("**/api/v1/concepts/by-symbol/**", (r) => r.fulfill({ json: {
    as_of: "2026-09-18", membership_as_of: "2026-09-18",
    items: [{ board_code: "BK0501", board_name: "次新股", pct_change: 4.66 },
      { board_code: "BK0714", board_name: "白酒概念", pct_change: null }] } }));
  await page.goto("/stock/600000");
  const tags = page.getByTestId("concept-tags");
  await expect(tags).toContainText("所属概念");                  // 正路径确实渲染该文案 → 反路径 count 0 才非空谈
  await expect(tags).toContainText("次新股");
  await expect(tags).toContainText("+4.66%");                     // 涨跌色 + 两位小数
  await expect(tags).toContainText("白酒概念");                    // pct_change = null → 只有名字，不得 0.00%
  await expect(tags).not.toContainText("白酒概念 0.00%");
  await tags.getByText("次新股").click();
  await expect(page).toHaveURL(/\/market\/concept\/BK0501/);
});

test("概念标签请求失败时整块不渲染，页面其余部分正常", async ({ page }) => {
  await page.route("**/api/v1/exchanges/Shanghai_Stocks/stocks/600000/enriched", (r) =>
    r.fulfill({ json: STOCK_600000 }));
  // 失败路径的断言必须在请求落地 + 一次渲染提交之后再下（见下方注释），先盯住失败事件
  const failed = page.waitForEvent("requestfailed", (req) => req.url().includes("/concepts/by-symbol/"));
  await page.route("**/api/v1/concepts/by-symbol/**", (r) => r.abort());
  await page.goto("/stock/600000");
  await failed;
  // `toHaveCount(0)` 不自动等待：紧跟 `goto` 断言会在 isLoading 期通过 ——
  // 「因为还没渲染」而不是「因为不渲染」，反证实验（把路由换成成功载荷）实测仍绿。
  // 双 rAF = 跨过 React 的提交与绘制，之后 DOM 已反映查询结果。
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => requestAnimationFrame(() => r(null)))));
  // 零占位：空壳/标题占位都不许留。断言**同时**收在 `所属概念` 文案与 testid 上 ——
  // 只断 testid 时，若回归把 testid 去掉却仍渲染「所属概念」空壳，用例会假绿。
  await expect(page.getByText("所属概念")).toHaveCount(0);
  await expect(page.getByTestId("concept-tags")).toHaveCount(0);
  await expect(page.getByRole("tab", { name: "概览" })).toBeVisible();
});

test("次新股情绪卡：KPI 与 items 同源、替代口径注脚、部分可判带出 n", async ({ page }) => {
  await page.route("**/api/v1/new-stocks", (r) => r.fulfill({ json: NEW_STOCKS_MIXED }));
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  // 断言全部收在卡内：情绪 Tab 其他卡也有 `--`/同名文案，全局断言会得到假绿
  const card = page.getByTestId("new-stock-board");
  await expect(card.getByText("未开板")).toBeVisible();
  await expect(card.getByText("平均涨跌幅", { exact: true })).toBeVisible();
  // T15 口径契约串（不得只在某处泛泛出现，必须收在本卡内）
  await expect(card.getByText(/非破发/)).toBeVisible();            // 替代口径必须声明
  await expect(card.getByText(/BK0501/)).toBeVisible();
  await expect(card.getByText(/上市 ≤1 年/)).toBeVisible();
  await expect(card.getByText(/成分每日 18:20 刷新/)).toBeVisible();
  // 成分口径同样读 `membership_as_of`（09-17），不是 `as_of`（09-18）
  await expect(card.getByText(/成分截至 9月17日/)).toBeVisible();
  // I3：未收录成分（stock_id IS NULL）既不在 items 也不进 KPI，必须显式披露（不得静默丢弃）
  await expect(card.getByText("另有 2 只成分股未收录（名录待刷新），未参与统计")).toBeVisible();
  // 正向未开板：1 只 `never_broken: true` ⇒ `1家`（不得因为另有不可判行而变 `--`）
  const unbrokenTile = card.locator(".ant-statistic", { hasText: "未开板" });
  await expect(unbrokenTile.locator(".ant-statistic-content")).toHaveText("1家");
  // M-b（评审修复）：1 只不可判时瓦片数值之外必须带出可判家数（3 只里 2 只可判），
  // 否则「未开板 1家」会被读成「没有一个不可判」——缺失 ≠ 0。
  await expect(card.getByText("限价缺失不可判 → -- · n=2 可判家数")).toBeVisible();
  // 不可判只影响「未开板」口径，不改写该行已有的行情：C沈鼓的连板照旧是 `2板`；
  // 而真正缺失的字段（C信诺维 streak=null）渲染 `--`，不得渲染 0 板（缺失 ≠ 0）。
  await expect(card.locator("tr", { hasText: "C沈鼓" }).locator("td").filter({ hasText: /^2板$/ })).toHaveCount(1);
  await expect(card.locator("tr", { hasText: "C信诺维" }).locator("td").filter({ hasText: /^--$/ })).toHaveCount(1);
});

test("次新股情绪卡全量不可判：未开板渲染 `--` 而非 0 家", async ({ page }) => {
  await page.route("**/api/v1/new-stocks", (r) => r.fulfill({ json: NEW_STOCKS_DEGRADED_SINGLE }));
  await page.goto("/market");
  await page.getByRole("tab", { name: "短线情绪" }).click();
  const card = page.getByTestId("new-stock-board");
  // 唯一 item 的 never_broken = null ⇒ 未开板不可判 → 数值是 `--`，不得渲染 0 家/已开板。
  // 断言必须收在瓦片的数值槽里：注脚文案 "限价缺失不可判 → --" 也含 `--`，全局取 `--` 会假绿。
  const unbrokenTile = card.locator(".ant-statistic", { hasText: "未开板" });
  await expect(unbrokenTile.locator(".ant-statistic-content")).toHaveText("--");
  await expect(card.getByText("限价缺失不可判 → -- · n=0 可判家数")).toBeVisible();
});
