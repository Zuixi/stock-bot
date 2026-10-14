import { test, expect as baseExpect } from "@playwright/test";

/**
 * 响应式四档断点 E2E（Phase 0.2，**先写先红**）。
 *
 * 依据：2026-10-13 全站设计评审实测——`/market/global` 在 390px 视口下
 * `documentElement.scrollWidth = 749px`（视口 390）→ 整页横向溢出，右侧内容被推出屏外
 * 且无法通过点击到达。本文件把它变成**每条路由 × 四档视口**的结构断言。
 *
 * 断点契约见计划 §5.1（四档：<768 / 768–1023 / 1024–1279 / ≥1280）。
 * 本文件用 `390 / 768 / 1280 / 1440` 代表四档的**边界样本**。
 *
 * 断言纪律（计划 §1「新断言必须绕开的两个已知坑」）：
 * 1. **只断布局/尺寸，不断数据**：这些路由可能未登录、可能空数据、可能 redirect 到
 *    `/login`——「登录页不许横向溢出」同样是有效断言，故**不因登录重定向而 skip**。
 * 2. **不与 loading 窗口赛跑**：测量前先等 `networkidle`（有界，超时不致命）再读
 *    `scrollWidth`；宽度是同步布局事实，不需要数值轮询。
 *
 * 预期红（Phase 0 完成判据）：`/market/global@390` 与 `/market/global@768` 溢出
 * （实测 `scrollWidth = 877`）。Phase 4（W4）把 `.table-scroll` 横向滚动容器补上后转绿。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** 四档断点的代表性视口（§5.1：<768 / 768–1023 / 1024–1279 / ≥1280） */
const VIEWPORTS = [390, 768, 1280, 1440];

/** 计划 §2 0.2 逐字给出的路由集合 */
const ROUTES = [
  "/",
  "/market",
  "/market/global",
  "/market/heatmap",
  "/company/600540",
  "/industry/pig",
  "/me",
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
        // 解构出的 `scrollWidth` 恒为 `undefined` → `toBeLessThanOrEqual` 报
        // "Matcher error: received value must be a number"，与溢出无关（实测 28/28 全红）。
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
