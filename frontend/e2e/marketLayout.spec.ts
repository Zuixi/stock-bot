import { test, expect as baseExpect } from "@playwright/test";

/**
 * `/market` 工作台信息密度 E2E（Phase 0.4，**先写先红**）。
 *
 * 依据：2026-10-13 全站设计评审实测三条——
 * 1. 「数据版图」钉在每个 Tab 底部（约 800px × 11 行，5 个 Tab 各看一遍）→ 不得出现在工作台；
 * 2. 「申万行业网格」与「行业分类（申万）」同屏渲染两遍 31 个行业（信息等价）→ 标题只许出现 1 次；
 * 3. `market-page-head` 的副标题是**手写字面量**（`全球指数 · A股全景 · 资金流向 · 龙虎榜/大宗/解禁/回购`），
 *    与 Tab labels 不同源 → 必须 === `items.map(i => i.label).join(" · ")`。
 *
 * 断言纪律（计划 §1 两个已知坑 + §2 0.4 的补充）：
 * - **负向断言（"页面不含数据版图"）在 loading 窗口内恒真**：故先 `networkidle`，再显式等
 *   该 Tab 的**锚点卡片**出现（文案锚点，非 `data-testid`），最后才断言"不含"。
 * - **计数用结构选择器**：`locator(选择器, { hasText })` 的 `toHaveCount(1)`，
 *   不用全局 `getByText`（中文文案在 SVG `<title>`、图例、嵌套容器里会重复）。
 * - **锚点卡片按 Tab 分别指定**：等的是"这个 Tab 真的渲染出来了"的证据，
 *   不是固定 sleep。
 *
 * 预期红（Phase 0 完成判据）：第 3 条**立即失败**（副标题字面量 ≠ Tab labels 拼接）。
 * 第 1、2 条在**当前分支**上已经是绿的——「数据版图」卡片早于本计划落地前就被
 * `1efe65a chore(market): 移除「数据版图」静态矩阵` 移出市场页（只保留在 landing）。
 * 该事实写入 task-23 报告：0.4 的红是 1 条不是 3 条，原因是**计划撰写时间早于那次移除**，
 * 不是断言写错。保留第 1 条断言，它的价值转为"回归闸"（不许把矩阵再加回工作台）。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

interface TabCase {
  /** Tab label（与 `market/index.tsx` 的 items[].label 同源） */
  label: string;
  /** 该 Tab 渲染后必然出现的**文案锚点**（用来等请求终态，而非固定 sleep） */
  anchor: string;
}

const TABS: TabCase[] = [
  { label: "指数总览", anchor: "全球市场" },
  { label: "A股全景", anchor: "申万行业网格" },
  { label: "资金流向", anchor: "资金" },
  { label: "数据面", anchor: "龙虎榜" },
  { label: "短线情绪", anchor: "涨停" },
];

/** 等 Tab 真正渲染：networkidle（有界）+ 锚点文案出现。两者都有界，避免 flaky。 */
async function settledOn(page: import("@playwright/test").Page, tab: string, anchor: string) {
  await page.getByTestId("market-tabs").getByRole("tab", { name: tab, exact: true }).click();
  await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
  await expect(page.getByText(anchor, { exact: false }).first()).toBeVisible();
}

test.describe("/market 工作台：去重复与口径同源", () => {
  test("每个 Tab 下页面文本都不含「数据版图」", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/market", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});

    for (const { label, anchor } of TABS) {
      await settledOn(page, label, anchor);
      // ⚠️ **Phase 1 收窄作用域（断言没被放宽，只是变精确）**：原先这里是全局
      // `page.getByText("数据版图")`。§W1.2 给一级导航补了模块内二级树之后，`/market`
      // 的侧栏会出现一个**导航节点**叫「数据版图」（它就是新页的入口名），全局匹配会命中
      // 侧栏标签 → ② 假红（实测：恰好 1 个命中，且 `closest('[data-testid=module-side-nav]')` 为真）。
      // ② 要守的是「工作台**正文**不再渲染那张矩阵」，导航标签是另一回事（它由 `MODULE_TREES`
      // 驱动，不是被搬回来的矩阵）。故**按容器身份**排除，而不是按文案排除——把矩阵搬回正文
      // 仍然会被抓住。为什么不用 `locator.filter({ hasNot })`：实测它在 1.62.1 上**不过滤**
      // （命中数仍为 1，假红照旧），故这里显式在浏览器里判容器身份，判据写死、可复核。
      const bodyHits = await page.evaluate(() => {
        const NAV = '[data-testid="primary-nav"],[data-testid="module-side-nav"],[data-testid="nav-drawer"]';
        const walk = (root: Element): string[] => {
          const out: string[] = [];
          for (const el of Array.from(root.querySelectorAll("*"))) {
            if (el.closest(NAV)) continue; // 导航容器内的文案不算工作台正文
            const own = Array.from(el.childNodes)
              .filter((n) => n.nodeType === Node.TEXT_NODE)
              .map((n) => n.textContent ?? "")
              .join("");
            if (own.includes("数据版图")) out.push(el.tagName + "." + String(el.className));
          }
          return out;
        };
        return walk(document.body);
      });
      // 判据是"正文里 0 处"，且**打印命中元素**便于排障（不是恒真的空断言）
      expect(
        bodyHits,
        `Tab「${label}」正文仍渲染「数据版图」（命中 ${bodyHits.join("、")}）——它应迁出工作台（§2.1）`
      ).toEqual([]);
    }
  });

  // ⚠️ 本用例此前是**空转的**：它数的是 `.ant-card-head-title` 里文案为「申万行业网格」的数量，
  // 而冗余的第二份列表标题是「行业分类（申万）」——文案不同，所以永远数到 1、永远通过。
  // 改为数「**渲染整份 31 行业列表的容器**」的数量（结构锚点，与标题文案无关）。
  test("同屏只渲染一份 31 行业列表（§2.2 要求二选一）", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    await expect(page.locator(".sw-grid").first()).toBeVisible(); // 先确认等到了目标区块

    // 一个"行业区块" = 渲染整份 31 行业列表的容器：瓦片网格（.sw-grid）
    // 或列表卡（.industry-level1-link，§2.2 删除前的形态）。
    const blocks = async () =>
      (await page.locator(".sw-grid").count()) +
      (await page.locator(".industry-level1-link").count());

    expect(await blocks(), "31 行业被等价渲染了两遍（§2.2 要求二选一）").toBe(1);

    // 正对照：注入第二份列表后，同一个 blocks() 必须读到 2。
    // 没有这一步，上一条断言无法与"选择器写错了、永远数到 1"区分开（旧写法就是这么失灵的）。
    await page.locator(".sw-grid").first().evaluate((node) => {
      node.parentElement?.appendChild(node.cloneNode(true));
    });
    expect(await blocks(), "正对照失败：计数断言是空转的（计数没跟着 DOM 变）").toBe(2);
  });

  test("market-page-head 副标题 === Tab labels 以 ` · ` 拼接（同源）", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/market", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});

    // Tab labels 只取 `market-tabs` 自己的 nav（页面里还有「亚洲/美洲」等嵌套 Segmented，
    // 全局 `.ant-tabs-tab` 会把它们一起抓进来）
    const labels = (
      await page.locator('[data-testid="market-tabs"] > .ant-tabs-nav .ant-tabs-tab').allInnerTexts()
    ).map((t) => t.trim());
    expect(labels.length, "未能读到 Tab labels（选择器失效）").toBeGreaterThan(0);

    const subtitle = (await page.locator(".market-page-head .ant-typography-secondary").innerText()).trim();
    expect(
      subtitle,
      `副标题与 Tab 不同源：副标题 =「${subtitle}」，Tab labels 拼接 =「${labels.join(" · ")}」` +
        `（§2.3 要求 items.map(i => i.label).join(" · ")）`
    ).toBe(labels.join(" · "));
  });
});
