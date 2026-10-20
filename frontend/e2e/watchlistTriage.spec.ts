import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * `/me?tab=triage`「今日体检」e2e（S8：spec §S8.1 / §S8.4 / §S8.5，计划 Task 5）。
 *
 * ## 为什么全部 mock
 *
 * 本文件的每条判据都只依赖**响应 → 渲染**这一段：条目数、分级分组、`folded` 折叠行、
 * `state` 的三态（全静默 / 无数据日 / 无自选）+ error、免责与禁用词、规则出处与规则说明
 * 抽屉（`GET /api/v1/watchlists/triage/rules`，抽屉打开才请求）。
 * 把 `GET /api/v1/watchlists/triage` 与 `/triage/rules` 用 `page.route` 钉住之后，
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
      // 规则出处（provenance）：卡片脚上方那行「规则 · 版本 · 数据表」的数据源
      rule_text: "收盘价 ≤ 20 日均线",
      rule_version: "r1",
      source_table: "daily_quotes",
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
      rule_text: "当日新增公告 ≥ 1 条",
      rule_version: "r1",
      source_table: "announcements",
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
      rule_text: "收盘价 = 近 60 交易日最高价",
      rule_version: "r1",
      source_table: "daily_quotes",
    },
  ],
  unavailable: [],
  unavailable_keys: [],
  disclaimer: "本产品只展示已发生的数据事实，不构成投资建议；历史数据不代表未来表现。",
};

/**
 * `GET /api/v1/watchlists/triage/rules` 的响应（逐字照 `contract.md` 的 `rules[]`）。
 *
 * 两条规则刻意覆盖「规则说明」抽屉的两种形态：
 * - `break_ma20`：`stats` 非空 → 展示信号/天数/每日条数/超额中位（**含正负号**）/最差分位；
 * - `new_high_low`：`stats=null` + `stats_note` → **只显示原因，不显示 0/空表**。
 *
 * `t1_excess_med: 0.30` 与 `t5_excess_med: -1.02` 一起出现，是为了同时钉住"正数补 +、
 * 负数带 -"——只放负值的话，"前端悄悄取了绝对值"这种回归也能绿。
 */
const RULES_FIXTURE = {
  rules: [
    {
      key: "break_ma20",
      label: "跌破 20 日线",
      severity: "risk",
      version: "r1",
      rule_text: "收盘价 ≤ 20 日均线（前复权）",
      definition: "收盘价落到 20 日均线下方，近一个月的平均成本已被跌破。",
      thresholds: { day_pct: -7.0, five_day_pct: -12.0 },
      source_tables: ["daily_quotes"],
      update_note: "盘后 15:30 起可由 daily_quotes 计算",
      failure_modes: [
        "跳空缺口时当日涨跌幅会失真",
        "停牌复牌首日的均线含缺失交易日",
        "除权当日未复权价会误判",
        "次新（上市不足 60 交易日）均线不稳",
      ],
      stats: {
        as_of: "2026-10-16",
        window_months: 12,
        signals: 1234,
        days: 240,
        per_day: 5.1,
        t1_excess_med: 0.3,
        t5_excess_med: -1.02,
        t20_excess_med: -1.23,
        t5_p05: -9.81,
        basis: "超额=信号组中位收益−当日全市场中位收益（5 日复权）",
      },
      stats_note: null,
    },
    {
      key: "new_high_low",
      label: "创 60 日新高/新低",
      severity: "attention",
      version: "r1",
      rule_text: "收盘价 = 近 60 交易日最高价",
      definition: "收盘价创 60 个交易日新高或新低。",
      thresholds: { window: 60 },
      source_tables: ["daily_quotes"],
      update_note: "盘后 15:30 起可由 daily_quotes 计算",
      failure_modes: ["停牌复牌首日的前高含缺失交易日"],
      stats: null,
      stats_note: "该规则依赖的数据不足 12 个月，暂不统计",
    },
  ],
};

/** 本页可见文本里**一个都不许出现**的判断词（spec §S8.5 禁用词表中与本页文案相关的子集）。 */
// 下面这行必须**逐字**列出禁用词才有断言对象，故给它行级豁免（扫描器会打印豁免行，评审可见）
const FORBIDDEN = ["买入", "卖出", "推荐", "看好", "建议关注", "目标价", "机会", "布局"]; // compliance-allow: 断言清单本身必须包含禁用词

/**
 * 市场状态一行（S10 §S10.3）的响应，**逐字照抄** spec 的 JSON 示例。
 *
 * 两个数刻意挑成"能证明没被补 0 / 没被取整"的形态：`median_pct: 0.8` 必须渲染成
 * `+0.8%`（带正号、一位小数）、`p05_pct: -3.1` 必须带负号；`duration_days: 7` 对应
 * 摘要里的「已 7 天」。`notes` 一并给上：它是契约字段，但**不上屏**（页面只写结论与口径）。
 */
const REGIME_FIXTURE = {
  state: "修复",
  since: "2026-10-08",
  duration_days: 7,
  transition: { 修复: 0.55, 亢奋: 0.2, 中性: 0.15, 冰点: 0.05, 退潮: 0.05 },
  forward: {
    "5d": { n: 37, median_pct: 0.8, p05_pct: -3.1 },
    "20d": { n: 36, median_pct: 1.4, p05_pct: -6.2 },
  },
  notes: ["状态由固定规则判定，只描述已发生的事实", "每态样本 <30 不给分布"],
  as_of: "2026-10-17",
  version: "v1",
};

/** 登录态 + triage 读面（含规则说明）的最小完整 mock（见文件头"假登录"一节）。 */
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
  // 体检卡片现在带「从自选移除」（2026-10-19），页面会读一次自选清单；
  // 不 mock 就会打到 dev proxy（本机 :80 有网关，返回 401）→ 全局清态 → 跳登录页。
  await page.route("**/api/v1/watchlists", (route) => route.fulfill({ status: 200, json: [] }));
  // 规则说明是**懒取**的（抽屉打开才请求）：卡片测试不点抽屉就不会打到这里，
  // 但一旦打开就必须有确定响应，否则会掉进真接口 401 → 全局清态的连锁（见文件头）。
  await page.route("**/api/v1/watchlists/triage/rules", (route) =>
    route.fulfill({ status: 200, json: RULES_FIXTURE })
  );
  // 顶部「市场状态一行」（S10 §S10.3）也是本页的读面：不 mock 就会打真接口，
  // 断言会随当天真实状态漂移（写死"修复"就假红、只断言"存在"就假绿）。
  // 需要 500 的用例在调用本函数**之后**再注册一条（Playwright 后注册的先命中）。
  await page.route("**/api/v1/market/regime", (route) =>
    route.fulfill({ status: 200, json: REGIME_FIXTURE })
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

  /**
   * 规则出处（provenance）第一条：卡片脚上方必须有一行「规则：人话阈值 · 版本 · 数据表」。
   *
   * 两个判据：
   * 1. 三个字段都真的上屏（不是只写了"规则"两个字）；`rule_version`/`source_table` 与
   *    `rules[]` 的 `version`/`source_tables` 同源，这里只钉卡片侧。
   * 2. **不得泄露探测器代码**：`detector`（`break_ma20`）是内部标识，卡片只该出现人话。
   *    断言按「数据 xxx」切成两段：`source_table` 是契约**要求**上屏的表名（`daily_quotes`），
   *    它本身就是 `单词_单词` 形态；探测器代码只可能出现在前一段（规则正文/版本）里，
   *    故对前一段做 `_[a-z]` 扫描，等价于"规则行里没有探测器代码"，又不会把表名误判成泄漏。
   */
  test("卡片有「规则：」出处行，且不泄露探测器代码", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.goto("/me?tab=triage");

    const ruleLines = page.getByTestId("triage-rule");
    await expect(ruleLines).toHaveCount(FIXTURE.items.length);

    const first = ruleLines.first();
    await expect(first).toContainText("规则：收盘价 ≤ 20 日均线");
    await expect(first).toContainText("· r1 ·");
    await expect(first).toContainText("数据 daily_quotes");

    // 探测器代码只可能落在「数据 」之前那一段：那里必须一个人话之外的机器串都没有
    const head = (await first.innerText()).split("数据 ")[0];
    expect(head, `规则行出现探测器代码形态的机器串：${head}`).not.toMatch(/_[a-z]/);

    // 更强的一条：整块体检区域不得出现任何一条 fixture 里的探测器 key。
    // 用标识符边界而不是 `toContain`：`announcement` 是 `announcements`（表名）的前缀，
    // 子串判定会把"合法的数据表名"误报成"泄漏了探测器 key"——宁可判得准，也不引入假红。
    const panelText = await page.locator(".triage").innerText();
    for (const detector of FIXTURE.items.map((item) => item.detector)) {
      // detector 只含 `[a-z0-9_]`，可直接拼进正则（无元字符）
      const leaked = new RegExp(`(?<![a-z0-9_])${detector}(?![a-z0-9_])`).test(panelText);
      expect(leaked, `体检面板出现探测器 key：${detector}\n${panelText}`).toBe(false);
    }
  });

  /**
   * 规则出处第二条：「规则说明」抽屉。
   *
   * 一条用例覆盖抽屉的两种形态（契约 `stats` 可为 null）：
   * - `break_ma20`：`stats` 非空 → 正文/失效场景/统计口径/「历史统计不代表未来表现」都要在，
   *   且超额中位**带 % 与正负号**（正数 `+0.30%`、负数 `-1.02%`）；
   * - `new_high_low`：`stats=null` → 只显示 `stats_note`，**不显示 0/空表/统计脚注**。
   */
  test("规则说明 Drawer：正文、失效场景、统计口径可见；stats=null 只显示原因", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.goto("/me?tab=triage");

    await page.getByTestId("triage-rule-open").first().click();
    const drawer = page.getByTestId("triage-rule-drawer");
    await expect(drawer).toBeVisible();

    // 标题带规则 label 与 version（不然用户不知道自己在看哪条规则的说明）
    const title = page.locator(".ant-drawer-title");
    await expect(title).toContainText("跌破 20 日线");
    await expect(title).toContainText("r1");

    // 定义 + rule_text
    await expect(drawer).toContainText("收盘价落到 20 日均线下方");
    await expect(drawer).toContainText("收盘价 ≤ 20 日均线（前复权）");
    // 数据来源
    await expect(drawer).toContainText("daily_quotes");
    await expect(drawer).toContainText("盘后 15:30 起可由 daily_quotes 计算");
    // 已知失效场景（第一条逐字）
    await expect(drawer).toContainText("跳空缺口时当日涨跌幅会失真");
    // 历史统计：正负号必须都在（各自钉一种符号，取绝对值/丢号的回归会红）
    await expect(drawer).toContainText("每日约 5.1 条");
    await expect(drawer).toContainText("+0.30%");
    await expect(drawer).toContainText("-1.02%");
    await expect(drawer).toContainText("-9.81%");
    // 统计脚注（硬要求）
    await expect(drawer).toContainText("历史统计不代表未来表现");
    await expect(drawer).toContainText("样本 1234 次");
    // 有 stats 时不该出现原因说明
    await expect(page.getByTestId("triage-rule-stats-note")).toHaveCount(0);

    // 换到 `stats=null` 的那条规则
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();

    await page.getByTestId("triage-rule-open").nth(2).click();
    await expect(drawer).toBeVisible();
    await expect(page.getByTestId("triage-rule-stats-note")).toHaveText(
      "该规则依赖的数据不足 12 个月，暂不统计"
    );
    // 不显示 0、空表或统计脚注：stats=null 是"没有样本"，不是"样本是 0"
    await expect(drawer).not.toContainText("每日约");
    await expect(drawer).not.toContainText("超额中位");
    await expect(drawer).not.toContainText("%");
    await expect(drawer).not.toContainText("历史统计不代表未来表现");
  });

  /**
   * 市场状态一行（S10，spec §S10.3）：`/me?tab=triage` **顶部**一行 + 点击展开。
   *
   * 逐字钉住 spec 给的形态 `市场状态：修复（已 7 天）｜历史同态 5 日中位 +0.8%（n=37）`，
   * 以及展开后的两块内容：**转移比例五态各一行**（按 spec §S10.2 的判定次序，不按概率
   * 重排）与 **20 日分布**（中位 / 最差 5% / n）。
   *
   * 三条负向判据同样重要：
   * - **不预测**（§S10.5）：文案里不得出现"即将/预测/建议"这类前瞻/建议措辞；
   * - **不出现机器串**：`forward` 的键是 `5d`/`20d`（后端 `dict[str, ...]`），它们是数据
   *   键而不是文案，页面上一个都不许有；
   * - **不给"未知"占位**：本用例证明的是"有数据时长什么样"，空态见下一条用例。
   */
  test("市场状态一行：摘要逐字可读，展开后能看到转移比例与 20 日分布", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.goto("/me?tab=triage");

    const summary = page.getByTestId("regime-summary");
    await expect(summary).toHaveText(
      "市场状态：修复（已 7 天）｜历史同态 5 日中位 +0.8%（n=37）"
    );

    // 位置：面板**顶部**——在「数据截至」之前（比坐标稳）
    const panelText = await page.locator(".triage").innerText();
    const regimeAt = panelText.indexOf("市场状态");
    const metaAt = panelText.indexOf("数据截至");
    expect(regimeAt).toBeGreaterThan(-1);
    expect(metaAt).toBeGreaterThan(-1);
    expect(regimeAt).toBeLessThan(metaAt);

    // 默认折叠：展开区不存在，`aria-expanded` 为 false（键盘/读屏可达）
    await expect(page.getByTestId("regime-detail")).toHaveCount(0);
    await expect(page.getByTestId("regime-toggle")).toHaveAttribute("aria-expanded", "false");

    await page.getByTestId("regime-toggle").click();
    await expect(page.getByTestId("regime-detail")).toBeVisible();
    await expect(page.getByTestId("regime-toggle")).toHaveAttribute("aria-expanded", "true");

    // 转移比例：五态各一行，次序 = spec §S10.2 的判定表
    const rows = page.getByTestId("regime-transition-row");
    await expect(rows).toHaveCount(5);
    const order = ["冰点", "修复", "亢奋", "退潮", "中性"];
    for (let i = 0; i < order.length; i += 1) {
      await expect(rows.nth(i)).toContainText(order[i]);
    }
    // 概率是 0–1 → 百分数（`0.55` → `55.0%`，`0.05` → `5.0%`）
    await expect(rows.nth(1)).toContainText("55.0%");
    await expect(rows.nth(0)).toContainText("5.0%");
    await expect(rows.nth(2)).toContainText("20.0%");

    // 颜色编码（S10 §S10.3，owner 2026-10-19 批准）：状态同时靠**色 + 文字 + 色块**三通道表达
    // —— 色条/色点带状态 class，图例每行一个色块，堆叠条段数 = 非零概率的状态数。
    await expect(page.getByTestId("regime-toggle")).toHaveAttribute("data-regime-state", "修复");
    await expect(page.getByTestId("regime-line")).toHaveClass(/regime--warming/);
    const segs = page.getByTestId("regime-transition-seg");
    // 五态概率都非零（5/55/20/15/5）→ 五段；零概率的状态**不渲染**段（不留 0 宽空段）
    await expect(segs).toHaveCount(5);
    await expect(page.locator(".triage__regime-swatch")).toHaveCount(5);
    // 色块与色段用的是**令牌变量**（不是写死色值）：确认样式确实引用了 --regime-*
    const barStyle = await page.locator(".triage__regime-bar").getAttribute("style");
    expect(barStyle).toBeNull(); // 容器不带内联色值（内联只在段宽上）

    // 20 日分布：中位 / 最差 5% / n 三个都要在，且**保留符号**（正数 `+`、负数 `-`）
    const forward = page.getByTestId("regime-forward-20d");
    await expect(forward).toContainText("中位");
    await expect(forward).toContainText("+1.4%");
    await expect(forward).toContainText("最差 5%");
    await expect(forward).toContainText("-6.2%");
    await expect(forward).toContainText("n=36");

    // 机器串不得上屏：horizon 键（`5d`/`20d`）与 ISO 日期都不许直出
    const detail = page.getByTestId("regime-detail");
    await expect(detail).not.toContainText("5d");
    await expect(detail).not.toContainText("20d");
    await expect(detail).not.toContainText(/T\d{2}:\d{2}/);

    // §S10.5：不预测、不建议、不给"未知"占位。
    // 作用域**只到这一行**：整块体检面板的免责原文里有「不构成投资建议」，
    // 对全面板做子串判定会把那句合规原文误判成"前瞻措辞"（假红）。
    const regimeText = await page.getByTestId("regime-line").innerText();
    for (const word of ["即将", "预测", "建议", "未知"]) {
      expect(regimeText, `市场状态一行出现禁用措辞：${word}`).not.toContain(word);
    }
  });

  /**
   * 市场状态接口失败（500）：**整行不渲染**，页面其余部分照常。
   *
   * 为什么"不渲染"比"显示未知"更难写对：很容易顺手写一个 `state ?? "未知"` 的占位，
   * 于是"这次没读到"被读成"市场状态是未知"——那是一个不存在的判定结果。契约要的是
   * "没有这一行"（spec §S10.3 + 后端 `state=null` 的空态设计）。
   *
   * 同时钉住一条**边界**：这条装饰性读面失败**不得**把用户踢下线。前端对它的 401/失败
   * 做了"不清态"处理（`fetchRegime` 的 `skipAuth`），故 500 之后体检清单、免责原文、
   * 复核入口必须一个不少地还在。
   */
  test("市场状态接口 500：整行不渲染（不留「未知」占位），页面其余部分正常", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    // 后注册先命中：覆盖 mockAuthedTriage 里的 200
    await page.route("**/api/v1/market/regime", (route) =>
      route.fulfill({
        status: 500,
        json: { code: "INTERNAL_ERROR", message: "regime unavailable", trace_id: "e2e" },
      })
    );
    await page.goto("/me?tab=triage");

    await expect(page.getByTestId("regime-line")).toHaveCount(0);
    await expect(page.getByTestId("regime-summary")).toHaveCount(0);
    // 不留"未知"占位：面板里连"市场状态"四个字都不该出现
    await expect(page.locator(".triage")).not.toContainText("市场状态");

    // 页面其余部分照常：条目数 = 响应条目数、免责原文、每条都有复核入口
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);
    await expect(page.locator(".triage__disclaimer")).toHaveText(FIXTURE.disclaimer);
    await expect(page.getByRole("link", { name: "复核" })).toHaveCount(FIXTURE.items.length);
    // 没有被踢去登录页（装饰性读面的失败不得触发全局清态）
    await expect(page).toHaveURL(/\/me\?tab=triage/);
    await expect(page.getByRole("tab", { name: /今日体检/ })).toHaveAttribute(
      "aria-selected",
      "true"
    );
  });

  /**
   * `state` 为空（产物缺失/损坏/落后 → 后端 `state=null`）：**整行不渲染**。
   *
   * 与上一条分工：500 是"没读到"，`state=null` 是"读到了、但结论是空"。两种都要隐身，
   * 因为对用户来说"这里没有这一行"是唯一诚实的呈现——`state` 为空**不是**"中性"。
   */
  test("市场状态为空（产物缺失）：整行不渲染，也不写「中性/未知」", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.route("**/api/v1/market/regime", (route) =>
      route.fulfill({
        status: 200,
        json: {
          ...REGIME_FIXTURE,
          state: null,
          since: null,
          duration_days: 0,
          transition: {},
          forward: {},
          notes: ["状态产物尚未生成"],
        },
      })
    );
    await page.goto("/me?tab=triage");

    await expect(page.getByTestId("regime-line")).toHaveCount(0);
    const panelText = await page.locator(".triage").innerText();
    expect(panelText).not.toContain("市场状态");
    expect(panelText).not.toContain("中性");
    expect(panelText).not.toContain("未知");
    // 体检清单照常在
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);
  });

  /**
   * 半截 payload（后端并行开发中 / 降级）：`{state}` 之外字段全缺省。
   *
   * 契约外的健壮性判据——一行字**不许**因为"缺 `forward`/`transition`"就把页面打崩
   * （那是本末倒置），也**不许**把缺的样本补成 `0%`：五个去向逐行写 `—`，20 日分布写
   * "暂无同态样本"。这是"`null` 与 0 必须区分"在本页的落点。
   */
  test("半截 payload（只有 state）：一行照常渲染，缺的样本写「—」而不是 0", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await page.route("**/api/v1/market/regime", (route) =>
      route.fulfill({ status: 200, json: { state: "修复" } })
    );
    await page.goto("/me?tab=triage");

    // 只有 state 可写：摘要就只写 state（不补"（已 0 天）"，也不编样本量）
    await expect(page.getByTestId("regime-summary")).toHaveText("市场状态：修复");

    await page.getByTestId("regime-toggle").click();
    const rows = page.getByTestId("regime-transition-row");
    await expect(rows).toHaveCount(5);
    for (let i = 0; i < 5; i += 1) {
      await expect(rows.nth(i)).toContainText("—");
    }
    // 没有样本 ≠ 样本是 0
    await expect(page.getByTestId("regime-forward-missing")).toBeVisible();
    await expect(page.getByTestId("regime-detail")).not.toContainText("0.0%");
  });

  test("卡片可「从自选移除」：/me 默认 tab 也能删（不必再切到自选 tab）", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    const deletes: string[] = [];
    // 后注册先命中（LIFO）：items/* 必须比上面的 watchlists 更具体、注册更晚
    await page.route("**/api/v1/watchlists", (route) =>
      route.fulfill({
        status: 200,
        json: [
          {
            id: "wl-1",
            user_id: USER.id,
            name: "默认自选",
            is_default: true,
            created_at: "2026-10-01T00:00:00Z",
            updated_at: "2026-10-01T00:00:00Z",
            items: [
              {
                id: "i-1",
                watchlist_id: "wl-1",
                symbol: "600519",
                exchange: "Shanghai_Stocks",
                sort_order: 0,
                notes: null,
                created_at: "2026-10-01T00:00:00Z",
                updated_at: "2026-10-01T00:00:00Z",
              },
            ],
          },
        ],
      })
    );
    await page.route("**/api/v1/watchlists/items/*", (route) => {
      const symbol = decodeURIComponent(new URL(route.request().url()).pathname.split("/").pop() ?? "");
      deletes.push(symbol);
      return route.fulfill({ status: 200, json: { deleted: true, symbol } });
    });

    await page.goto("/me?tab=triage");
    const removeBtn = page.getByRole("button", { name: "从自选移除 600519" }).first();
    await expect(removeBtn).toBeVisible();
    await removeBtn.click();
    // antd 会在两个汉字之间插空格（autoInsertSpace）⇒ 可访问名是「移 除」
    await page.locator(".ant-popover").getByRole("button", { name: /移\s*除/ }).click();

    await expect.poll(() => deletes).toEqual(["600519"]);
    await expect(page.getByText("已从自选移除 600519")).toBeVisible();
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

/**
 * ── S12.1 / S12.2：体检 → 记一笔 → 到期提醒 ────────────────────────────────
 *
 * 这个 `describe` 覆盖的是一条**跨 tab 的链路**（体检卡片 → 决策日志表单 → 提交），
 * 所以两边的读面都得 mock：triage 由 `mockAuthedTriage` 钉住，journal 由
 * `mockJournalTab` 钉住。少 mock 一边，用例就会因为"真接口 401 → 全局清态"而假红
 * （见文件头的"假登录"一节），而不是因为被测行为错了。
 *
 * 判据刻意按**整条链**写，而不是三个孤立事实：
 * - 只断言 href 会漏掉"点进去没预填"；
 * - 只断言预填会漏掉"提交后参数没清、刷新又带一次同一个标的"；
 * - 只断言提示出现会漏掉"0 条 / 取不到时也画一行"。
 */

/** 「记一笔」表单要用的最小 journal mock（列表/统计/汇总/创建四条读面）。 */
async function mockJournalTab(page: Page, opts: { due?: number } = {}) {
  const store: {
    entries: Record<string, unknown>[];
    lastCreate: Record<string, unknown> | null;
  } = { entries: [], lastCreate: null };

  // 只注册**一条** `**/api/v1/journal**`，在 handler 里按 pathname + method 分派：
  // 多条正则容易因为"后注册的先命中"把 `/summary` 吞进 `/journal` 那条（同 decisionJournal 的教训）。
  await page.route("**/api/v1/journal**", async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/\/+$/, "");
    const method = request.method();

    if (path.endsWith("/summary")) {
      // spec §S12.2：`{open, due, reviewed}`；`due` 是三角标里唯一会显示的那个数
      return route.fulfill({
        status: 200,
        json: { open: 0, due: opts.due ?? 0, reviewed: 0 },
      });
    }

    if (path.endsWith("/stats")) {
      // 样本不足：只写"样本不足"，与角标/带入无关，但要有个确定形状（否则统计卡报错）
      return route.fulfill({
        status: 200,
        json: { n: 0, reviewed_n: 0, avg_excess_pct: null, discipline_break_share: null },
      });
    }

    if (method === "POST" && path === "/api/v1/journal") {
      const body = JSON.parse(request.postData() ?? "{}");
      store.lastCreate = body;
      const created = {
        id: 900,
        symbol: body.symbol ?? null,
        kind: body.kind ?? "watch",
        thesis: body.thesis,
        invalidation: body.invalidation,
        horizon_days: body.horizon_days,
        snapshot_summary: "价格 1588.00 元 · 当日 +0.40%",
        created_at: "2026-10-18T09:00:00Z",
        due_on: "2026-11-17",
        status: "open",
        reviewable: true,
        evaluation: null,
        review: null,
      };
      store.entries.unshift(created);
      return route.fulfill({ status: 201, json: created });
    }

    if (method === "GET" && path === "/api/v1/journal") {
      const status = url.searchParams.get("status");
      return route.fulfill({
        status: 200,
        json: { items: store.entries.filter((item) => !status || item.status === status) },
      });
    }

    return route.fulfill({
      status: 404,
      json: { code: "NOT_FOUND", message: `e2e mock 未覆盖：${method} ${path}`, trace_id: "e2e" },
    });
  });

  return store;
}

test.describe("一键记一笔（S12.1：体检 → 决策日志）", () => {
  test("卡片「记一笔」→ 表单已预填标的 + 带入提示；提交后 query 被清", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    const journal = await mockJournalTab(page);
    await page.goto("/me?tab=triage");

    // ① 每张卡片都有「记一笔」，href 逐字是契约里的那条（不新增路由，只带 query）
    const links = page.getByTestId("triage-journal");
    await expect(links).toHaveCount(FIXTURE.items.length);
    await expect(links.nth(0)).toHaveAttribute(
      "href",
      "/me?tab=journal&symbol=600519&from=triage"
    );
    await expect(links.nth(1)).toHaveAttribute(
      "href",
      "/me?tab=journal&symbol=000001&from=triage"
    );

    await links.nth(0).click();

    // ② 落点：仍在 `/me`（只换 tab），带入参数在 URL 里
    await expect(page).toHaveURL(/\/me\?tab=journal&symbol=600519&from=triage$/);
    await expect(page.getByTestId("journal-panel")).toBeVisible();

    // ③ 预填：标的已带过来 + 一行「从今日体检带入」（说明从哪来、快照不用你填）
    await expect(page.getByTestId("journal-symbol")).toHaveValue("600519");
    await expect(page.getByTestId("journal-prefill-hint")).toHaveText(
      "从今日体检带入：600519（快照在提交时自动生成）"
    );
    // 期限默认 30 天（spec §S12.1「仍可改」）
    await expect(
      page.getByTestId("journal-horizon").locator(".ant-radio-button-wrapper-checked")
    ).toHaveText("30 天");

    // ④ **三必填照旧**：预填只带标的，thesis / invalidation 一个字都没替用户写
    await expect(page.getByTestId("journal-thesis")).toHaveValue("");
    await expect(page.getByTestId("journal-submit")).toBeDisabled();
    await expect(page.getByTestId("journal-missing")).toContainText("判断依据");
    await expect(page.getByTestId("journal-missing")).toContainText("失效条件");
    await expect(page.getByTestId("journal-missing")).not.toContainText("观察期限");

    // ⑤ 填完提交：载荷带带入的标的 + 默认 30 天
    await page.getByTestId("journal-thesis").fill("体检看到跌破 20 日线，想看能不能修复");
    await page.getByTestId("journal-inv-kind").click();
    await page.locator(".ant-select-item-option", { hasText: "跌破某价格" }).first().click();
    await page.getByTestId("journal-inv-price").fill("1500");
    await page.getByTestId("journal-submit").click();

    await expect(page.getByTestId("journal-open-item")).toHaveCount(1);
    expect(journal.lastCreate).toMatchObject({ symbol: "600519", horizon_days: 30 });

    // ⑥ 提交成功后清掉 `symbol` / `from`：刷新不会再带一次同一个标的；`tab` 必须保住
    await expect(page).toHaveURL(/\/me\?tab=journal$/);
    await expect(page.getByTestId("journal-prefill-hint")).toHaveCount(0);

    // ⑦ 刷新后仍是空表单（参数真的没了，不是只把提示藏起来）
    await page.reload();
    await expect(page.getByTestId("journal-panel")).toBeVisible();
    await expect(page.getByTestId("journal-prefill-hint")).toHaveCount(0);
    await expect(page.getByTestId("journal-symbol")).toHaveValue("");
  });
});

test.describe("到期提醒（S12.2：站内，无推送）", () => {
  test("due > 0：面板顶部一行「有 N 条判断今日到期 → 去复盘」", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await mockJournalTab(page, { due: 3 });
    await page.goto("/me?tab=triage");

    const hint = page.getByTestId("triage-due-hint");
    await expect(hint).toBeVisible();
    await expect(hint).toHaveText("有 3 条判断今日到期 → 去复盘");
    // 去处：日志 tab 的 due 视图（不新增路由）
    await expect(hint).toHaveAttribute("href", "/me?tab=journal&status=due");

    // 位置：面板**顶部**——在「数据截至」之前（按文本顺序断言，比坐标稳）
    const panelText = await page.locator(".triage").innerText();
    expect(panelText.indexOf("今日到期")).toBeGreaterThan(-1);
    expect(panelText.indexOf("今日到期")).toBeLessThan(panelText.indexOf("数据截至"));
  });

  test("due = 0 与 summary 失败：都不渲染这一行（不显示 0）", async ({ page }) => {
    await mockAuthedTriage(page, FIXTURE);
    await mockJournalTab(page, { due: 0 });
    await page.goto("/me?tab=triage");

    // 0 条到期时不写"0 条到期"：那行字会被读成"今天没事"，而它只是"没有到期条目"
    await expect(page.getByTestId("triage-due-hint")).toHaveCount(0);
    await expect(page.locator(".triage")).not.toContainText("条判断今日到期");
    // 体检清单本身照常
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);

    // summary 挂了：同为"不知道"，同样不渲染（把"不知道"画成"0 条"正是最坏的一种假话）
    await page.route("**/api/v1/journal/summary", (route) =>
      route.fulfill({
        status: 500,
        json: { code: "INTERNAL_ERROR", message: "summary unavailable", trace_id: "e2e" },
      })
    );
    await page.goto("/me?tab=triage");
    await expect(page.getByTestId("triage-due-hint")).toHaveCount(0);
    // 两个读面互不牵连：体检清单仍在，页面也没被踢去登录
    await expect(page.getByTestId("triage-item")).toHaveCount(FIXTURE.items.length);
    await expect(page).toHaveURL(/\/me\?tab=triage/);
  });
});
