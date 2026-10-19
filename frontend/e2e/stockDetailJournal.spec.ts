import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { test, expect as baseExpect, type Page } from "@playwright/test";

/**
 * P2-2 E2E：个股详情页动作区的「记一笔」。
 *
 * `/company/:symbol` 是**公开路由**（`route-manifest.ts` 的 `company-symbol` 没有
 * `requiresAuth`），所以本文件的**主要**判据是匿名分支：
 *
 * 1. 匿名用户在动作区看到的是 `<Link to="/login">登录后记一笔</Link>`，**不是**按钮 ——
 *    点它直接落到 `/login`。这条守的是"别让用户先写 200 字判断依据、点提交才 401"。
 * 2. 匿名时表单**根本没挂载**（`journal-form` 计数为 0）：不是"点了才报错"，
 *    而是"从来不给你填的机会"。
 * 2b. **切换标的后动作区按新标的重新渲染**（review round 1：`recorded` state 不得跨标的残留）。
 *    匿名分支只能守到"没有残留 UI"，"state 是否真的清空"由静态 `key` 断言 + 已登录分支兜
 *    （原因见该用例内的注释：跨标的导航本来就会换掉整棵子树，DOM 哨兵无法区分有无 key）。
 * 3. 已登录分支（真登录 → 按钮 → 弹窗 → 预填并锁定代码 → 提交 → 变成「已记录 · 查看」）
 *    需要真凭据 `E2E_RESEARCH_USER` / `E2E_RESEARCH_PASSWORD`（与 `research.spec.ts`
 *    同一套环境变量契约，见 docs/testing/frontend-e2e.md）。凭据缺失时**显式 skip**，
 *    绝不 mock `/auth/session` 造一个假登录态：本仓库已经踩过这个坑
 *    （`research.spec.ts` 文件头：只 mock session 会让其余真接口 401 → 全局
 *    `auth:unauthorized` 清态 → 布局反复重挂，测出来的东西与真实路径无关）。
 * 4. 因为 (3) 在没有凭据的环境里不会执行，另配一条**接线断言**（读组件源码）：
 *    已登录分支必须仍然把 `EntryForm` 挂在 `Modal` 里、传 `defaultSymbol`、
 *    成功后失效 `["journal"]` 并把按钮换成 `/me?tab=journal` 链接。
 *    它**证明不了渲染**（只有真登录那条能），但能在没有凭据的 CI 上挡住"整条已登录
 *    分支被误删/改错"这类回归 —— 否则那部分代码在本机永远没有守卫。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const STOCK = "/stock/600519";
const WIDE = { width: 1440, height: 900 };

const E2E_USER = process.env.E2E_RESEARCH_USER ?? "";
const E2E_PASSWORD = process.env.E2E_RESEARCH_PASSWORD ?? "";

/** 打开详情页并等到报价条（动作区宿主）渲染。 */
async function openDetail(page: Page) {
  await page.setViewportSize(WIDE);
  await page.goto(STOCK);
  await expect(page.getByTestId("quote-bar")).toBeVisible();
}

/** 真登录（`GET /auth/csrf` → `POST /auth/login` 带 `X-CSRF-Token` 双提交）。 */
async function login(page: Page) {
  const csrf = (await (await page.request.get("/auth/csrf")).json()).csrf_token as string;
  const res = await page.request.post("/auth/login", {
    headers: { "X-CSRF-Token": csrf },
    data: { username_or_email: E2E_USER, password: E2E_PASSWORD },
  });
  expect(res.status(), "e2e 登录失败（凭据无效 / auth-service 未就绪）").toBe(200);
}

/** 登出当前会话（非幂等方法需 csrf cookie 与 `X-CSRF-Token` 双提交相等）。 */
async function logout(page: Page) {
  const csrf = (await page.context().cookies()).find((c) => c.name === "stockbot_csrf")?.value;
  if (!csrf) return;
  await page.request.post("/auth/logout", { headers: { "X-CSRF-Token": csrf } });
}

/**
 * 表单里选失效条件（AntD Select：点开下拉再点选项）。
 * 与 `decisionJournal.spec.ts` 同款（那条路径已被既有用例验证过）。
 */
async function pickInvalidationKind(page: Page, label: string) {
  await page.getByTestId("journal-inv-kind").click();
  await page.locator(".ant-select-item-option", { hasText: label }).first().click();
}

/**
 * 表单里选期限。**不能**对 `getByRole("radio")` 直接 `check()`：AntD 的 `Radio.Button`
 * 把真 `<input>` 渲染成 `opacity:0` 的覆盖层，Playwright 判它不可见，会一直重试到超时；
 * 用户点的其实是可见的 label 外壳（与 `decisionJournal.spec.ts` 同一处踩坑记录）。
 */
async function pickHorizon(page: Page, label: string) {
  await page
    .getByTestId("journal-horizon")
    .locator(".ant-radio-button-wrapper", { hasText: label })
    .first()
    .click();
}

test.describe("匿名分支（公开路由）", () => {
  test("未登录：动作区是「登录后记一笔」链接，点它落到 /login", async ({ page }) => {
    await openDetail(page);

    const link = page.getByTestId("journal-login-link");
    await expect(link).toBeVisible();
    await expect(link).toHaveText("登录后记一笔");
    await expect(link).toHaveAttribute("href", "/login");

    // 未登录时**不能**有「记一笔」按钮：否则点开就是一个提交必 401 的表单
    await expect(page.getByTestId("journal-entry-button")).toHaveCount(0);

    await link.click();
    await expect(page).toHaveURL(/\/login$/);
    // 真的到了登录页（不是空白路由）：输入框与提交按钮都在。
    // 按钮名用正则：AntD 会在两个汉字之间插一个空格（渲染成「登 录」），
    // `{ name: "登录", exact: true }` 会因此匹配不到。
    await expect(page.getByPlaceholder("用户名 / 邮箱")).toBeVisible();
    await expect(page.getByRole("button", { name: /^登\s*录$/ })).toBeVisible();
  });

  test("未登录：表单根本没有挂载（不是点了才报错）", async ({ page }) => {
    await openDetail(page);

    // 表单/弹窗都不该在 DOM 里 —— 「记一笔」不是一个"看得见但会失败"的入口
    await expect(page.getByTestId("journal-form")).toHaveCount(0);
    await expect(page.getByTestId("journal-thesis")).toHaveCount(0);
    await expect(page.locator(".ant-modal")).toHaveCount(0);
  });

  test("切换标的：动作区按新标的重新渲染，不残留上一只股票的状态", async ({ page }) => {
    /**
     * review round 1 的回归靶子：「已记录 · 查看」曾经只存在于组件内部 `recorded` state 里，
     * 组件若不在 symbol 变化时重挂，用户从 600519 记完一笔再切到 600000，就会在一只
     * **没有任何记录**的股票上读到「已记录 · 查看」——页面说了假话。
     *
     * ## 这条用例守什么、守不到什么（写清楚，避免被当成"已证明状态隔离"）
     *
     * 守：跨标的**客户端导航**之后，动作区确实按新标的重新渲染 —— 只有一个记一笔入口、
     * 匿名时它是 `href=/login` 的链接、既不出现 `已记录 · 查看` 也不出现表单/弹窗。
     *
     * 守不到：`recorded` 这个 **state 是否被清空**。匿名分支根本走不到那个状态
     * （它只可能由"提交成功"置位），所以任何匿名的 DOM 断言都无法直接观察它。
     * 实测（本轮）：给动作区的 `<a>` 盖一个 JS 属性当"节点身份哨兵"、再切回来，
     * 哨兵在**未加 key 的版本上也会消失** —— 跨标的导航时整棵 QuoteBar 子树本来就会被换掉
     * （页面在目标标的无缓存时会退回整页 `Spin`，有缓存时也会重建），于是"节点是否复用"
     * 不能区分有没有 key。那种断言会是一条**永远绿**的假用例，故没有留下。
     *
     * 真正确定性的守卫是静态那一条（`QuoteBar.tsx` 里 `key={stock.symbol}`），
     * 加上有凭据时才会跑的「已登录分支」。这条用例作为"切标的后动作区仍然正确"的冒烟。
     */
    await openDetail(page);
    await expect(page.getByTestId("journal-login-link")).toBeVisible();

    // 顶栏搜索是客户端路由跳转（不整页刷新），这是"两只股票直接切换"的真实路径
    const search = page.getByPlaceholder("搜索代码/名称");

    await search.fill("600000");
    await page.locator(".ant-select-item-option", { hasText: "600000" }).first().click();
    await expect(page).toHaveURL(/\/company\/600000/);
    await expect(page.getByTestId("quote-bar")).toContainText("600000");

    // 切过去之后：动作区只有一个入口，且是匿名链接（没有把上一只的"已记录"带过来）
    await expect(page.getByTestId("journal-login-link")).toHaveCount(1);
    await expect(page.getByTestId("journal-recorded-link")).toHaveCount(0);
    await expect(page.getByTestId("journal-entry-button")).toHaveCount(0);
    await expect(page.getByTestId("journal-form")).toHaveCount(0);
    await expect(page.locator(".ant-modal")).toHaveCount(0);

    // 切回来同样成立
    await search.fill("600519");
    await page.locator(".ant-select-item-option", { hasText: "600519" }).first().click();
    await expect(page).toHaveURL(/\/company\/600519/);
    const link = page.getByTestId("journal-login-link");
    await expect(link).toHaveCount(1);
    await expect(link).toHaveText("登录后记一笔");
    await expect(link).toHaveAttribute("href", "/login");
    await expect(page.getByTestId("quote-bar")).toContainText("600519");
  });
});

test.describe("已登录分支（需要真凭据）", () => {
  test.beforeEach(async ({ page }) => {
    test.skip(
      !E2E_USER || !E2E_PASSWORD,
      "需 E2E_RESEARCH_USER / E2E_RESEARCH_PASSWORD（见 docs/testing/frontend-e2e.md）；" +
        "缺失时本分支不执行，由「接线断言」用例兜底"
    );
    await login(page);
  });

  // 收尾登出：每次都会新建一个会话（TTL 24h），不回收会在 auth 库/Redis 里越积越多
  test.afterEach(async ({ page }) => {
    await logout(page);
  });

  test("记一笔：代码预填并锁定，提交后不跳转、按钮变「已记录 · 查看」", async ({ page }) => {
    await openDetail(page);

    const button = page.getByTestId("journal-entry-button");
    await expect(button).toBeVisible();
    await expect(button).toHaveText("记一笔");
    // 动作区里「加入自选」仍是唯一的主操作（primary）
    await expect(page.getByTestId("quote-bar").getByRole("button", { name: /自选/ })).toBeVisible();

    await button.click();
    const modal = page.locator(".ant-modal-content");
    await expect(modal).toBeVisible();
    await expect(modal).toContainText("记一笔 · 600519");

    // 标的被预填并锁定（disabled）：用户已经在 600519 的页面上，不该能改成别的代码
    const symbol = page.getByTestId("journal-symbol");
    await expect(symbol).toHaveValue("600519");
    await expect(symbol).toBeDisabled();

    // 关掉再开 = 空表单（不残留上一次的草稿）
    await page.getByTestId("journal-thesis").fill("这是一段会被丢弃的草稿");
    await modal.getByRole("button", { name: /取\s*消/ }).click();
    await expect(page.locator(".ant-modal-content")).toBeHidden();

    await button.click();
    await expect(page.getByTestId("journal-thesis")).toHaveValue("");
    await expect(page.getByTestId("journal-symbol")).toHaveValue("600519");

    // 填满 3 个必填后提交
    const thesis = "渠道库存回到常态，价格低于成本线就说明看错了";
    await page.getByTestId("journal-thesis").fill(thesis);
    await pickInvalidationKind(page, "跌破某价格");
    await page.getByTestId("journal-inv-price").fill("1500");
    await pickHorizon(page, "30 天");
    await expect(page.getByTestId("journal-submit")).toBeEnabled();
    await page.getByTestId("journal-submit").click();

    // 成功的可见反馈 + 按钮切成链接；**停留在本页**（记一笔不是"跳去管理日志"）
    await expect(page.getByText("已记下这一笔")).toBeVisible();
    const recorded = page.getByTestId("journal-recorded-link");
    await expect(recorded).toBeVisible();
    await expect(recorded).toHaveText("已记录 · 查看");
    await expect(recorded).toHaveAttribute("href", "/me?tab=journal");
    await expect(page).toHaveURL(/\/company\/600519/);
    await expect(page.locator(".ant-modal-content")).toBeHidden();
  });
});

test.describe("接线断言（无凭据时的静态兜底）", () => {
  /**
   * 读组件源码断言**已登录分支仍然接线完整**。
   *
   * 为什么不 mock `/auth/session` 来跑真弹窗：那是假登录态（见文件头第 3 条）。
   * 为什么还要这一条：没有凭据的环境里，上面整个 `已登录分支` 会被 skip，
   * 于是"按钮 → 弹窗 → EntryForm(defaultSymbol) → 失效 journal 缓存 → 换成
   * /me?tab=journal 链接"这条链路**零守卫**。静态断言挡不住渲染回归，但挡得住
   * "分支被删掉/键名写错"这类最常见的破坏，且不引入任何假状态。
   */
  test("JournalEntryAction 仍把 EntryForm 挂在 Modal 里，并接上 defaultSymbol 与 journal 失效", () => {
    const source = readFileSync(
      fileURLToPath(
        new URL("../src/features/stock-detail/components/JournalEntryAction.tsx", import.meta.url)
      ),
      "utf8"
    );

    expect(source, "已登录分支的按钮不见了").toContain("journal-entry-button");
    expect(source, "弹窗没有挂 EntryForm").toContain("<EntryForm");
    expect(source, "EntryForm 没有拿到当前股票代码").toContain("defaultSymbol={symbol}");
    expect(source, "提交成功后没有失效 journal 查询").toContain(
      'invalidateQueries({ queryKey: ["journal"] })'
    );
    expect(source, "提交后没有换成「查看」链接").toContain('to="/me?tab=journal"');
    expect(source, "提交后没有关弹窗").toContain("setOpen(false)");
    expect(source, "未登录分支没有登录链接").toContain('to="/login"');
    // 重新打开必须是空表单：靠"关闭即卸载"而不是逐个字段清空
    expect(source, "EntryForm 不是按 open 条件挂载的（重开可能留着上一次草稿）").toContain(
      "{open && ("
    );

    /**
     * 状态必须**按标的隔离**（review round 1 的 Important 项）。`recorded` 只有在
     * `recordedSymbol === symbol` 时才成立，"是否记过"被派生出来而不是存成一个布尔 ——
     * 这样即使 symbol 变化时组件没有重挂（事实如此：`/company/:symbol` 是同一个路由元素），
     * 「已记录 · 查看」也不可能漂到另一只股票上。上面那条切标的用例守运行时，
     * 这条钉住"机制本身没被改回布尔"。
     */
    expect(
      source,
      "记过的状态又被存成了布尔 ⇒ 换标的时会把「已记录 · 查看」带过去"
    ).toContain("const recorded = recordedSymbol === symbol");
    expect(
      source,
      "弹窗的打开状态没有按标的绑定 ⇒ 切标的时 A 的草稿会留在 B 的弹窗里"
    ).toContain("const open = openSymbol === symbol");
    expect(source, "提交成功时没有把标的写进 state").toContain("setRecordedSymbol(symbol)");
  });
});
