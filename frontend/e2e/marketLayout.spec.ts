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

/**
 * P1-1 第一刀：A股全景第一行的三面板并排（对标产品行情中心的「三面板一行」形态）。
 *
 * **作用域纪律**（计划 §1 的已知坑）：`板块主力资金流` 在 `资金流向` Tab 里**也有一张**
 * （本轮刻意保留页级 Tab，属已知重复，报告里明写）。因此这里的定位器一律先收进
 * `[data-testid="market-section-panorama"]` 再数卡 —— 全局 `getByText` /
 * `.ant-card:has-text("板块主力资金流")` 会同时命中另一个 Tab 的同名卡（AntD 把非激活
 * Tab 的 children 渲染在 `display:none` 容器里，仍留在 DOM 中），计数与几何都会失真。
 */
const PANORAMA_SECTION = '[data-testid="market-section-panorama"]';

/** 三块面板的卡头标题（与各组件里的 `Card title` 逐字一致；作用域限定后才唯一）。 */
const PANORAMA_PANEL_TITLES = ["A股涨跌分布", "板块热力图", "板块主力资金流"] as const;

/** 第一行三块面板的卡（按标题顺序返回，作用域限定在分区壳内）。 */
function panoramaRow(page: import("@playwright/test").Page): import("@playwright/test").Locator {
  return page
    .locator(`${PANORAMA_SECTION} .ant-card`)
    .filter({ has: page.locator(":scope > .ant-card-head") })
    .filter({ hasText: /A股涨跌分布|板块热力图|板块主力资金流/ });
}

test.describe("P1-1：A股全景第一行三面板（并排 / 堆叠 / 各自口径）", () => {
  test("1440 下三块面板并排（同一 y、x 递增），且各带自己的「数据截至」", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    await expect(page.locator(".sw-grid").first()).toBeVisible(); // 等到目标 Tab 真的渲染完

    // ① 作用域内**恰好 3 张**第一行面板卡（不是 2 张，也不是把别的卡算进来）
    await expect(
      panoramaRow(page),
      "A股全景第一行不是三块面板（少一块 = 回退成两栏；多一块 = 选择器把下面的卡也算进来了）"
    ).toHaveCount(PANORAMA_PANEL_TITLES.length);

    // ② 每块都还在，且各带自己的「数据截至」（口径纪律：不得靠分区层一个 as_of 顶替三块的口径）
    for (const title of PANORAMA_PANEL_TITLES) {
      const card = page.locator(`${PANORAMA_SECTION} .ant-card`).filter({ hasText: title });
      await expect(card, `面板「${title}」不在分区壳内`).toHaveCount(1);
      await expect(
        card.getByText(/数据截至/).first(),
        `面板「${title}」缺自己的「数据截至」`
      ).toBeVisible();
    }

    // ②b 口径徽标（收盘/未完整/按最近完整日）：**只有信封带 `as_of_quality` 的两块**能印。
    // `GET /market/sector-moneyflow` 的载荷实测只有 `{as_of, stale_days, items}`（无 `as_of_quality`），
    // 所以「板块主力资金流」卡**按既有契约不印徽标** —— 这不是本刀的缺失，且补它需要改后端
    // （本刀明令不动后端）。故这里对两块点名要求徽标（防"顺手把徽标删了"回归），对第三块
    // 只要求它不谎报：**不得**出现任何口径徽标文案（印了才是错——那会是无来源的口径声明）。
    for (const title of ["A股涨跌分布", "板块热力图"]) {
      await expect(
        page.locator(`${PANORAMA_SECTION} .ant-card`).filter({ hasText: title })
          .getByText(/收盘|未完整|按最近完整日/).first(),
        `面板「${title}」缺口径徽标`
      ).toBeVisible();
    }
    await expect(
      page.locator(`${PANORAMA_SECTION} .ant-card`).filter({ hasText: "板块主力资金流" })
        .locator(".section-card__quality"),
      "板块主力资金流卡印了口径徽标 —— 该端点不返回 as_of_quality，印出来就是无来源的口径声明"
    ).toHaveCount(0);

    // ③ 几何：三块同一 y（并排）、x 严格递增（顺序 = 涨跌分布 → 热力图 → 资金流）、宽度相近
    const boxes = await Promise.all(
      PANORAMA_PANEL_TITLES.map(async (title) => {
        const box = await page.locator(`${PANORAMA_SECTION} .ant-card`).filter({ hasText: title }).boundingBox();
        expect(box, `面板「${title}」没有 boundingBox`).not.toBeNull();
        return box!;
      })
    );
    const xOf = boxes.map((b) => Math.round(b.x));
    const yOf = boxes.map((b) => Math.round(b.y));
    const wOf = boxes.map((b) => Math.round(b.width));
    // 诊断串：断言红时不必回看代码就知道实际排成了几行
    const diag = `x=[${xOf.join(", ")}] y=[${yOf.join(", ")}] w=[${wOf.join(", ")}]`;
    expect(yOf[0], `三块面板不并排（第 2 块换行了）：${diag}`).toBe(yOf[1]);
    expect(yOf[0], `三块面板不并排（第 3 块换行了）：${diag}`).toBe(yOf[2]);
    expect(xOf[0], `面板顺序不是 涨跌分布 → 热力图 → 资金流：${diag}`).toBeLessThan(xOf[1]);
    expect(xOf[1], `面板顺序不是 涨跌分布 → 热力图 → 资金流：${diag}`).toBeLessThan(xOf[2]);
    expect(Math.min(...wOf), `三栏宽度不等（xl=8 应等宽）：${diag}`).toBeGreaterThan(300);
    expect(Math.max(...wOf) - Math.min(...wOf), `三栏宽度不等（xl=8 应等宽）：${diag}`).toBeLessThan(4);

    // ④ 正对照（证明上面的计数/几何断言不是空转）：
    //    在**同一个作用域**里克隆一块面板卡塞回行内 → 同一个 locator 必须读到 4 张。
    //    没有这一步，"toHaveCount(3) 通过"无法与"选择器永远匹配到 3 个东西"区分开
    //    （本文件第二条用例的 `.sw-grid` 断言就有过这种假绿前科）。
    await page
      .locator(`${PANORAMA_SECTION} .ant-card`)
      .filter({ hasText: "A股涨跌分布" })
      .evaluate((node) => node.parentElement?.appendChild(node.cloneNode(true)));
    expect(
      await panoramaRow(page).count(),
      "正对照失败：克隆一块面板后计数没变 —— 计数断言是空转的"
    ).toBe(PANORAMA_PANEL_TITLES.length + 1);
  });

  test("390 下三块面板各自独占一行（1-up 堆叠），且页面不横向溢出", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 900 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    await expect(page.locator(".sw-grid").first()).toBeVisible();

    await expect(panoramaRow(page)).toHaveCount(PANORAMA_PANEL_TITLES.length);
    const boxes = await Promise.all(
      PANORAMA_PANEL_TITLES.map(async (title) => {
        const box = await page
          .locator(`${PANORAMA_SECTION} .ant-card`)
          .filter({ hasText: title })
          .boundingBox();
        expect(box, `面板「${title}」没有 boundingBox`).not.toBeNull();
        return box!;
      })
    );
    // 堆叠判据：三块 x 相同、y 严格递增（并排的话 y 会相等）
    expect(boxes[0].x, "390 下第一块起点不一致").toBeCloseTo(boxes[1].x, 0);
    expect(boxes[1].x, "390 下第二块起点不一致").toBeCloseTo(boxes[2].x, 0);
    expect(boxes[0].y, "390 下未堆叠（1-up 失效）").toBeLessThan(boxes[1].y);
    expect(boxes[1].y, "390 下未堆叠（1-up 失效）").toBeLessThan(boxes[2].y);
    // 各块不得超出视口（溢出就是被推出屏外，点不到）
    for (const [i, box] of boxes.entries()) {
      expect(
        Math.round(box.x + box.width),
        `面板「${PANORAMA_PANEL_TITLES[i]}」超出 390 视口（x+w = ${Math.round(box.x + box.width)}）`
      ).toBeLessThanOrEqual(391);
    }
  });
});
