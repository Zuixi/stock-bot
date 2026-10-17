import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * `/me?tab=triage`「今日体检」e2e（S8：spec §S8.1 / §S8.4 / §S8.5，计划 Task 5）。
 *
 * ## 为什么全部 mock
 *
 * 本文件的每条判据都只依赖**响应 → 渲染**这一段：条目数、分级分组、`folded` 折叠行、
 * `state` 的三态（全静默 / 无数据日 / 无自选）+ error、免责与禁用词。
 * 把 `GET /api/v1/watchlists/triage` 用 `page.route` 钉住之后，
 * 判据**不随当天行情变化**，也不依赖后端新端点是否已上线/本机是否有自选（否则
 * "今天恰好没命中"会让"有条目"那条假红，"自选恰好为空"又会让它假绿）。
 *
 * ## 只 mock session 是"假登录"——这里把页面消费的读面一起 mock
 *
 * `research.spec.ts` 的教训（`signalPanel.spec.ts` 文件头有完整版）：没真 cookie 时
 * 只 mock `/auth/session` 会让其它真接口 401 → 全局 `auth:unauthorized` 清态 →
 * 页面反复重挂。故本文件把 `/auth/session`、`/auth/csrf` 与 triage 读面**一起** mock，
 * 没有真接口参与，不存在"部分真、部分假"造成的清态循环。
 *
 * ## 禁用词断言的边界
 *
 * spec §S8.5 的禁用词表以 `backend/scripts/triage_compliance.py` 为唯一真相（文档不复制第二份），
 * 这里只取**本页文案会碰到的子集**做运行时兜底：词表全集由后端合规脚本（Task 6）静态扫描，
 * 本文件负责"渲染出来的可见文本里一个都不许有"。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const USER = {
  id: "usr-e2e-triage",
  username: "triage_reader",
  email: "triage@example.com",
  display_name: "体检读者",
  status: "active",
  is_superuser: false,
  roles: ["reader"],
  permissions: ["stocks:read", "watchlist:manage"],
  created_at: "2026-10-01T00:00:00Z",
};

/**
 * 响应契约逐字照抄 spec §S8.4 + 集成收尾后的两个新字段：
 * `state`（三态：ok / no_data / no_watchlist）与 `unavailable`（**人话 label**，
 * 机器 key 只在 `unavailable_keys`，禁止上屏）。
 */
const FIXTURE = {
  as_of: "2026-10-17",
  state: "ok",
  quiet_count: 3,
  folded: 2,
  items: [
    {
      symbol: "600519",
      name: "贵州茅台",
      detector: "break_ma20",
      severity: "risk",
      headline: "跌破 20 日线",
      facts: [
        { label: "收盘价", value: 1588.0, unit: "元" },
        { label: "20 日线", value: 1601.2, unit: "元" },
      ],
      as_of: "2026-10-17",
      source: "daily_quotes",
      verify: "kline",
    },
    {
      symbol: "000001",
      name: "平安银行",
      detector: "announcement",
      severity: "event",
      headline: "披露三季度业绩快报",
      facts: [{ label: "公告类型", value: "业绩快报", unit: "" }],
      as_of: "2026-10-17",
      source: "announcements",
      verify: "announcement",
    },
    {
      symbol: "300750",
      name: "宁德时代",
      detector: "new_high_low",
      severity: "attention",
      headline: "创 60 日新高",
      facts: [{ label: "收盘价", value: 231.5, unit: "元" }],
      as_of: "2026-10-17",
      source: "daily_quotes",
      verify: "kline",
    },
  ],
  unavailable: [],
  unavailable_keys: [],
  disclaimer: "本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。",
};

/** 本页可见文本里**一个都不许出现**的判断词（spec §S8.5 禁用词表中与本页文案相关的子集）。 */
// 下面这行必须**逐字**列出禁用词才有断言对象，故给它行级豁免（扫描器会打印豁免行，评审可见）
const FORBIDDEN = ["买入", "卖出", "推荐", "看好", "建议关注", "目标价", "机会", "布局"]; // compliance-allow: 断言清单本身必须包含禁用词

/** 登录态 + triage 读面的最小完整 mock（见文件头"假登录"一节）。 */
async function mockAuthedTriage(page: Page, payload: unknown) {
  await page.route("**/auth/session", (route) =>
    route.fulfill({ status: 200, json: USER })
  );
  await page.route("**/auth/csrf", (route) =>
    route.fulfill({ status: 200, json: { csrf_token: "csrf-token-test" } })
  );
  await page.route("**/api/v1/watchlists/triage*", (route) =>
    route.fulfill({ status: 200, json: payload })
  );
}

test.describe("今日体检（/me?tab=triage）", () => {
  test("有条目：事实、数据截至、复核与免责可见，条目数 = 响应条目数，无判断词", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.goto("/me?tab=triage");

    // §S8.4 门禁：页面条目数 = 响应条目数（前端不得二次过滤/合并）
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);

    // 发生了什么（含数值）
    await expect(page.getByText("跌破 20 日线")).toBeVisible();
    await expect(page.getByText("披露三季度业绩快报")).toBeVisible();
    await expect(page.getByText("创 60 日新高")).toBeVisible();
    await expect(page.getByText("1588元")).toBeVisible();

    // 数据截至（走 shared/ui/date.ts 的格式化入口，不直出 ISO 机器串）
    const firstCard = page.getByTestId("triage-item").first();
    await expect(firstCard).toContainText("数据截至 10月17日");
    await expect(firstCard).not.toContainText(/T\d{2}:\d{2}/);

    // 复核：kline → /stock/:symbol，announcement → /news（brief 的映射契约）
    const verifies = page.getByRole("link", { name: "复核" });
    await expect(verifies).toHaveCount(FIXTURE.items.length);
    await expect(verifies.nth(0)).toHaveAttribute("href", "/stock/600519");
    await expect(verifies.nth(1)).toHaveAttribute("href", "/news");
    await expect(verifies.nth(2)).toHaveAttribute("href", "/stock/300750");

    // 分级分组：只出现 风险 / 事件 / 关注（本条 fixture 没有 background）
    await expect(page.getByRole("heading", { name: /风险/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /事件/ })).toBeVisible();
    await expect(page.getByRole("heading", { name: /关注/ })).toBeVisible();

    // §S8.4：超出上限的条数在顶部明示，不丢数据也不偷偷丢
    await expect(page.getByText("另有 2 项未展示")).toBeVisible();

    // §S8.5：免责声明可见——**逐字等于后端 `disclaimer`**（不是页面自己写的另一份；
    // 全局 footer 里也有「不构成投资建议」，故这里按容器定位而不是全页文本匹配）
    await expect(page.locator(".triage__disclaimer")).toHaveText(FIXTURE.disclaimer);

    // §S8.5：可见文本零禁用词
    const text = await page.locator("body").innerText();
    const hits = FORBIDDEN.filter((word) => text.includes(word));
    expect(hits, `页面可见文本出现禁用词：${hits.join("、")}`).toEqual([]);
  });

  test("全静默：明确写「都平静」，且不是空态占位", async ({ page }) => {
    await mockAuthedTriage(page, { ...FIXTURE, items: [], quiet_count: 12, folded: 0 });
    await page.goto("/me?tab=triage");

    await expect(page.getByText(/都平静/)).toBeVisible();
    await expect(page.getByText(/都平静（12 只）/)).toBeVisible();
    await expect(page.getByTestId("triage-item")).toHaveCount(0);
    // 全静默 ≠ 无自选：不得同时出现"去添加自选"的引导
    await expect(page.getByRole("link", { name: "添加自选" })).toHaveCount(0);
  });

  test("无自选：给「添加自选」入口，不留白页", async ({ page }) => {
    await mockAuthedTriage(page, {
      ...FIXTURE,
      state: "no_watchlist",
      items: [],
      quiet_count: 0,
      folded: 0,
    });
    await page.goto("/me?tab=triage");

    await expect(page.getByText(/还没有自选股/)).toBeVisible();
    const add = page.getByRole("link", { name: "添加自选" });
    await expect(add).toBeVisible();
    // 入口必须指向自选 tab（既有路由，不新建 /today）
    await expect(add).toHaveAttribute("href", "/me?tab=watchlist");
    // 无自选 ≠ 全静默
    await expect(page.getByText(/都平静/)).toHaveCount(0);
  });

  test("/me 不带 tab 时默认落在今日体检", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.goto("/me");

    await expect(page.getByRole("tab", { name: /今日体检/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);
  });

  test("无数据日：`state=no_data` 说的是「数据没就绪」，不是「没有自选」", async ({ page }) => {
    // 第四态：有自选，但最近完整交易日还不存在。修 bug 前这里会被渲染成
    // 「还没有自选股」——把"今天查不了"说成"你没加自选"，是本页最坏的一种误导。
    await mockAuthedTriage(page, {
      ...FIXTURE,
      state: "no_data",
      items: [],
      quiet_count: 0,
      folded: 0,
      unavailable: ["跌破 20 日线", "所属板块在动"],
      unavailable_keys: ["break_ma20", "sector_move"],
    });
    await page.goto("/me?tab=triage");

    await expect(page.getByText(/最近一个完整交易日的数据还没就绪/)).toBeVisible();
    // 不能与另外两种空态撞车
    await expect(page.getByText(/还没有自选股/)).toHaveCount(0);
    await expect(page.getByRole("link", { name: "添加自选" })).toHaveCount(0);
    await expect(page.getByText(/都平静/)).toHaveCount(0);
    // as_of 在 no_data 下是服务端当天兜底值，不得当作"数据日"上屏
    await expect(page.getByText(/数据截至/)).toHaveCount(0);
  });

  test("缺数据：不静默当作无事件（页面留痕），且不把内部 key 贴到屏幕上", async ({ page }) => {
    await mockAuthedTriage(page, {
      ...FIXTURE,
      items: [],
      quiet_count: 0,
      folded: 0,
      // 契约：unavailable 是**人话 label**；key 只在 unavailable_keys 里（禁止上屏）
      unavailable: ["上了龙虎榜", "所属板块在动"],
      unavailable_keys: ["dragon_tiger", "sector_move"],
    });
    await page.goto("/me?tab=triage");

    // §S8.4：「探测器缺数据 → 进 unavailable，**不静默当作无事件**」
    await expect(page.getByText(/2 项检查因数据暂缺未执行/)).toBeVisible();
    // 人话 label 必须真的上屏（否则用户只知道"有检查没跑"，不知道是哪几项）
    await expect(page.getByText(/上了龙虎榜、所属板块在动/)).toBeVisible();

    const text = await page.locator("body").innerText();
    expect(text, "内部探测器 key 不该出现在屏幕上").not.toContain("dragon_tiger");
    // 更强的一条：整块体检区域不得出现任何 `xxx_yyy` 形态的机器串
    // （漏一个 key 就命中；同时不会误伤中文文案与「10月17日」这类日期）
    const panelText = await page.locator(".triage").innerText();
    expect(panelText, `体检面板出现机器串：${panelText}`).not.toMatch(/[a-z]+_[a-z_]+/);
  });

  test("防回归：/me?tab=watchlist 仍是自选清单", async ({ page }) => {
    await page.route("**/auth/session", (route) => route.fulfill({ status: 200, json: USER }));
    await page.route("**/auth/csrf", (route) =>
      route.fulfill({ status: 200, json: { csrf_token: "csrf-token-test" } })
    );
    // 空自选：只断言面板骨架在（不引入 enriched 的第二处 mock）
    await page.route("**/api/v1/watchlists", (route) => route.fulfill({ status: 200, json: [] }));

    await page.goto("/me?tab=watchlist");

    await expect(page.getByRole("tab", { name: /自选/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
    await expect(page.getByText("我的自选（0）")).toBeVisible();
    await expect(page.getByText("暂未添加自选股")).toBeVisible();
    // 默认 tab 改了，但显式 tab=watchlist 不得被 triage 抢走
    await expect(page.getByTestId("triage-item")).toHaveCount(0);
  });
});
