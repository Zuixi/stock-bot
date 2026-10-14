import { test, expect as baseExpect } from "@playwright/test";

/**
 * 响应式四档断点 E2E（Phase 0.2，**先写先红**）。
 *
 * 依据：2026-10-13 全站设计评审实测——`/market/global` 在 390px 视口下
 * `documentElement.scrollWidth = 749px`（视口 390）→ 整页横向溢出，右侧内容被推出屏外
 * 且无法通过点击到达。本文件把它变成**每条路由 × 四档视口**的结构断言。
 *
 * 断点契约见计划 §5.1（四档：<768 / 768–1023 / 1024–1279 / ≥1280）
 * 与 `docs/design/landing-market-theme.md` 的「响应式断点契约」一节。
 * 本文件用 `390 / 768 / 1280 / 1440` 代表四档的**边界样本**。
 *
 * 断言纪律（计划 §1「新断言必须绕开的两个已知坑」）：
 * 1. **只断布局/尺寸，不断数据**：这些路由可能未登录、可能空数据、可能 redirect 到
 *    `/login`——「登录页不许横向溢出」同样是有效断言，故**不因登录重定向而 skip**。
 * 2. **不与 loading 窗口赛跑**：测量前先等 `networkidle`（有界，超时不致命）再读
 *    `scrollWidth`；宽度是同步布局事实，不需要数值轮询。
 *
 * 预期红（Phase 0 完成判据）：`/market/global@390` 与 `/market/global@768` 溢出
 * （Phase 0/1/2 实测 `scrollWidth = 877`；Phase 2 因数字字体换成 `--font-num` 漂到 **874**）。
 * **Phase 3 转绿**，实际溢出的元素与处置见 §4.2 与下方 `TAB_CASES` 的注释。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** 四档断点的代表性视口（§5.1：<768 / 768–1023 / 1024–1279 / ≥1280） */
const VIEWPORTS = [390, 768, 1280, 1440];

/** 计划 §2 0.2 逐字给出的路由集合 + Phase 3 补的 `/login`（见下） */
const ROUTES = [
  "/",
  "/market",
  "/market/global",
  "/market/heatmap",
  "/company/600540",
  "/industry/pig",
  "/me",
  /**
   * `/login` —— **Phase 3 §4.2 补入**。
   *
   * 为什么必须补：`/industry/pig` 与 `/me` 在匿名态**重定向**到 `/login`，于是它们在
   * 上面 7 条里测到的其实是**登录页**。而"登录页"本身从来没被**点名**断言过——
   * 一旦登录页自己溢出（它也是 §Why-now 表里"移动端横向溢出"的同族），
   * 红的会是一条**别的路由**，排查方向从一开始就是错的。点名后：
   * `/login` 红 = 登录页自己的问题；`/me` 红 = 重定向链路或登录页的问题。
   */
  "/login",
];

test.describe("响应式：四档视口下不得横向溢出", () => {
  for (const width of VIEWPORTS) {
    for (const route of ROUTES) {
      test(`${route}@${width} 无横向溢出`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(route, { waitUntil: "domcontentloaded" });
        // 有界等待请求终态：数据加载完成后布局才稳定。超时**不失败**——
        // 本断言测的是"当前布局宽度"，不是"多久加载完"（避免把慢网络误判成设计问题）。
        await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});

        // ⚠️ 必须 `evaluate` 返回**数字**：计划 §2 0.2 写的 `page.evaluate(() => document.documentElement)`
        // 在 Playwright 里实际拿到的是 `ref: <Node>` 字符串（不可序列化的 DOM 节点被替换成引用标记），
        // 解构出的 `scrollWidth` 恒为 `undefined` → `toBeLessThanOrEqual`
        // 报 "Matcher error: received value must be a number"，与溢出无关（实测 28/28 全红）。
        // 那也是一种"红"，但是**错误原因的红**——故这里直接取原始数字，让红只可能来自真溢出。
        const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
        const inner = await page.evaluate(() => window.innerWidth);
        // eslint 缺席，故把"测的是 documentElement"写进断言消息，排障时不必回看代码
        expect(
          scrollWidth,
          `${route}@${width} 横向溢出（documentElement.scrollWidth = ${scrollWidth}，视口 ${inner}）`
        ).toBeLessThanOrEqual(inner + 1);
      });
    }
  }
});

/**
 * **Tab 级**横向溢出扫描（Phase 3 §4.2b，计划 §4.2 的补充项）。
 *
 * 为什么单列一组：上面那 28 条只覆盖**路由级**布局。`/market` 的 5 个 Tab 是
 * **同一条路由**下的 5 套完全不同的布局，`page.goto("/market")` 只可能落在默认 Tab 上
 * ⇒ 另外 4 个 Tab 的溢出**没有任何门禁守着**。Phase 0 的实测就抓到了活例：
 * `/market?tab=moneyflow@390` `scrollWidth = 420 > 390`（成因是
 * `.mf-continuity__tag` 这个 `white-space: nowrap` 的口径 badge 不可压缩，
 * 把 `.mf-continuity__head` → 卡片 → `Col`（`min-width: auto`）一路顶宽；
 * 处置见 `SectorMoneyflowCard.css` 的 `max-width: 100%` + 省略号）。
 *
 * 视口只用 `390` 与 `768`：溢出是"窄屏"问题（≥1280 下这 5 个 Tab 在 Phase 0
 * 的 `scrollHeight` 实测里都没有横向溢出）；再加两档会让运行时间翻倍而信息量为零。
 */
const TAB_CASES = [
  { key: "overview", label: "指数总览" },
  { key: "panorama", label: "A股全景" },
  { key: "moneyflow", label: "资金流向" },
  { key: "dataface", label: "数据面" },
  { key: "sentiment", label: "短线情绪" },
] as const;

test.describe("响应式：/market 五个 Tab 在窄屏下不得横向溢出", () => {
  for (const width of [390, 768]) {
    for (const { key, label } of TAB_CASES) {
      test(`/market?tab=${key}（${label}）@${width} 无横向溢出`, async ({ page }) => {
        await page.setViewportSize({ width, height: 900 });
        await page.goto(`/market?tab=${key}`, { waitUntil: "domcontentloaded" });
        await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
        // 断言前先确认 Tab **真的切过去了**：URL 参数写错（例如 key 与实现漂移）时，
        // AntD Tabs 会静默停在默认 Tab，于是本用例测的是"指数总览"——
        // 又是一条"空转通过"的假绿。
        // ⚠️ 选择器必须限定到 `.market-tabs > .ant-tabs-nav`（**直接子级导航条**）：
        // 页面内嵌的「全球指数」卡自带一层 Tabs（亚洲/美洲），
        // 裸写 `[data-testid="market-tabs"] .ant-tabs-tab-active` 会同时命中两层
        // （实测 strict mode violation：`overview` 与 `asia` 都是 active）。
        const activeTab = page.locator(
          '[data-testid="market-tabs"] > .ant-tabs-nav .ant-tabs-tab-active'
        );
        await expect(activeTab, `Tab 未切到「${label}」——select 与实现的 key 漂移了`).toContainText(
          label
        );
        const scrollWidth = await page.evaluate(() => document.documentElement.scrollWidth);
        const inner = await page.evaluate(() => window.innerWidth);
        expect(
          scrollWidth,
          `/market?tab=${key}@${width} 横向溢出（documentElement.scrollWidth = ${scrollWidth}，视口 ${inner}）`
        ).toBeLessThanOrEqual(inner + 1);
      });
    }
  }
});

/**
 * §4.1 的**可读性**判据：指数卡名称不得被截到不可辨识。
 *
 * 与上面"不溢出"是两条独立的判据——**只压宽度就能让溢出消失**（例如给名称加
 * `overflow: hidden` 再压到 10px），所以必须同时钉住"名称还能读出来"，
 * 否则修溢出的代价就是把 §1 目标 5 反着违反一遍。
 *
 * ⚠️ 作用域必须限定到**首张卡**：`上证指数` 在「A股核心指数」与「全球指数」两处同时存在，
 * 全局 `getByText('上证指数')` 必撞 strict mode（计划 §1 的已知坑）。故先按卡片**标题**
 * 收到「A股核心指数」那张卡，再取 `first()`。
 *
 * ⚠️ **不得用行情数值当锚点**（例如 `filter({ hasText: "3,797.24" })`——计划 §5 W4 验收原文
 * 就是这么写的）：那是**实盘价格**，同一个交易日的两次运行都可能不同。用它当锚点会让
 * 断言在数据波动时"找不到元素"而红，红的原因是数据而不是布局——正是 §2.1 那类
 * "错误原因的红"。用标题（结构性文案）当锚点才是稳定的。
 */
test.describe("响应式：窄屏下指数卡名称可辨识（§4.1）", () => {
  for (const width of [390, 768]) {
    test(`/market@${width} 首张指数卡名称完整`, async ({ page }) => {
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market", { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      // 等到名称真的上屏（react-query settle）再断言，避免在 loading 窗口内空转
      const board = page.locator(".ant-card").filter({ hasText: "A股核心指数" }).first();
      const name = board.locator(".global-index-card__name").first();
      await expect(name).toHaveText("上证指数");
      // 名称被截断的判据是 `scrollWidth > clientWidth`（`toHaveText` 抓不到
      // "元素里有完整文本但视觉上只显示 3 个字"——那是 CSS 截断，不是文本缺失）
      const clipped = await name.evaluate((el) => el.scrollWidth > el.clientWidth + 1);
      expect(clipped, `「上证指数」被 CSS 截断（@${width}）`).toBe(false);
    });
  }
});
