import { test, expect as baseExpect, type Locator } from "@playwright/test";

/**
 * 一级导航可见性 E2E（Phase 0.3，**先写先红**）。
 *
 * 依据：2026-10-13 全站设计评审实测——顶栏 10 个一级导航在 1440px 下**折叠 3 项**进
 * `...`（1280px 下折叠 4 项），即「策略引擎 / 风险管理 / 交易执行」这些入口在一级导航里
 * **根本看不见**（要先点 `...` 才知道存在）。本文件把"10 项全部可见"变成断言。
 *
 * 形态无关（§0-A 裁定 = A1，导航将从 Header 移入左侧图标栏）：
 * 作用域锚点是 `data-testid="primary-nav"`（MainLayout 的导航外壳），
 * 标签锚点是 `data-nav-label="<标题>"`（NavTree 写入）——**不含** `.ant-menu` / `header`
 * 这类形态选择器。A1 落地后本断言只会由红转绿，不会变成"找不到锚点"的空跑。
 *
 * ⚠️ **为什么不能直接 `toBeVisible()`**（本文件写完后实测的教训）：
 * AntD 的 `...` 折叠**不隐藏**被折叠的菜单项——它把 `.ant-menu-overflow-item` 留在
 * DOM 里（`display` 保持 `block`、尺寸非 0），靠祖先 `.ant-menu-overflow` 的
 * `overflow: hidden` **裁掉**。而 Playwright 的可见性判定**只看元素自身与祖先的
 * `display/visibility`，不计祖先 overflow 裁剪**，于是 10 项全部报 `isVisible() === true`
 * ——断言假绿（实测：@1440 `toBeVisible()` 10/10 通过，而用户只看得到 7 项）。
 * 因此这里把"可见"定义为**几何可见**：元素盒子必须落在导航容器与视口的交集内。
 *
 * 预期红（Phase 0 完成判据）：`@1440` 3 项不可见（策略引擎 / 风险管理 / 交易执行），
 * `@1280` 4 项不可见（价值网图谱 + 同上 3 项）。W1 落地后两条都转绿。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** 计划 §2 0.3 的两个视口 */
const VIEWPORTS = [1280, 1440];

/** 10 个一级导航的标题（`NAV_ROOTS` 持有 path+标题，这里按渲染事实逐个核对） */
const NAV_LABELS = [
  "市场",
  "资讯",
  "行业分析",
  "公司分析",
  "宏观分析",
  "情绪分析",
  "价值网图谱",
  "策略引擎",
  "风险管理",
  "交易执行",
];

/** 与既有 e2e 同址的会话 mock：`research:read` 是 `/industry` 与 `/sentiment` 的可见性门禁。
 *  `roles` 必须留在前端白名单内（`trader`），否则会按"非法角色"清空会话。 */
const MOCK_USER = {
  id: "u-nav-1",
  username: "nav_probe",
  email: "nav@example.com",
  display_name: "导航探针",
  status: "active",
  roles: ["trader"],
  permissions: ["stocks:read", "research:read"],
};

/** 元素是否**真的显示给用户**：命中测试（`elementFromPoint`）。
 *
 *  为什么不用 `toBeVisible()`，也不只看 boundingBox：AntD 把被折叠的 `.ant-menu-overflow-item`
 *  留成 `height: 0`，却让内部 `<span>` 仍有 28×17 的盒子（实测 @1440：`策略引擎` 的
 *  `<li>` 为 112×0，而它的 `<span>` 仍报 x=173,y=23,w=56,h=17）。于是"span 有盒子"
 *  完全不能证明它可见。命中测试直接问浏览器"这个坐标上最顶层的元素是谁"——
 *  祖先 overflow 裁剪、`height: 0`、被其它元素覆盖，**全部**会体现在答案里。
 *  形态无关：A1 换成左侧图标栏后同样适用。 */
async function isHitTestable(locator: Locator) {
  return locator.evaluate((el) => {
    const hit = (node: Element | null): boolean => {
      if (!node || !(node instanceof HTMLElement)) return false;
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) return false;
      const x = Math.round(rect.left + rect.width / 2);
      const y = Math.round(rect.top + rect.height / 2);
      if (x < 0 || y < 0 || x >= window.innerWidth || y >= window.innerHeight) return false;
      const top = document.elementFromPoint(x, y);
      return !!top && (top === node || node.contains(top));
    };
    // 先测标签自身（文案节点）；标签被裁到 0 高时，回落到它的菜单项容器再测一次
    return hit(el) || hit(el.closest("li") ?? el.parentElement);
  });
}

test.describe("一级导航：1280 / 1440 下 10 项全部可见", () => {
  for (const width of VIEWPORTS) {
    test(`@${width} 一级导航 10 项全部可见`, async ({ page }) => {
      // 未登录时 6 个 authenticated 项被隐藏（可见性三态），"10 项可见"就失去意义，
      // 故以"已登录且持 research:read"为前置——mock 会话，不碰真实鉴权。
      await page.route("**/auth/**", (route) =>
        route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(route.request().url().includes("/session") ? MOCK_USER : { ok: true }),
        })
      );
      await page.setViewportSize({ width, height: 900 });
      await page.goto("/market", { waitUntil: "domcontentloaded" });

      const nav = page.getByTestId("primary-nav");
      await expect(nav).toBeVisible();

      // ① 10 个标签必须都渲染（防"清单改了、导航漏项"后断言静默变少）
      const hidden = [];
      for (const label of NAV_LABELS) {
        const item = nav.locator(`[data-nav-label="${label}"]`);
        await expect(item, `一级项「${label}」未渲染`).toHaveCount(1);
        if (!(await isHitTestable(item))) hidden.push(label);
      }

      // ② 一次性报出全部不可见项（逐条 expect 会在第一项就中断，看不到"折叠了哪几项"）
      expect(
        hidden,
        `@${width} 一级导航有 ${hidden.length} 项对用户不可见（被折进 "..." 或推出容器）：` +
          `${hidden.join("、")} —— 契约是 10 项全部可见（§0-A 裁定 A1 落地后转绿）`
      ).toEqual([]);
    });
  }
});

// 回归守护（Phase 1 视觉复核实测补的修复）：模块二级树**只按 pathname 选树**时，
// 匿名访问公开占位路由 `/news` 会渲染资讯的 4 个子节点，而「资讯」本身对匿名不可见
// ⇒ 侧栏出现 4 个**没有父级的孤儿导航项**（普通交易员访问 /sentiment 同理，要 research:read）。
// 不变量：二级树只在「该路径属于某个**可见**的一级导航」时才渲染。
test.describe("模块二级树不得出现孤儿项", () => {
  test("匿名 @1440：/news 不渲染资讯子树，/market 正常渲染（正对照）", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });

    // ① 匿名 + /news：资讯不在匿名可见的一级导航里 ⇒ 它的 4 个子节点必须是 0
    await page.goto("/news", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    const sidebar = page.getByTestId("module-side-nav");
    for (const label of ["上市公司公告", "政府官媒政策", "研究资讯", "市场资讯"]) {
      await expect(
        sidebar.locator(`[data-nav-label="${label}"]`),
        `孤儿导航项「${label}」：父级「资讯」对匿名不可见，不该单独出现`
      ).toHaveCount(0);
    }

    // ② 正对照：同一套选择器 + 同一条路径前缀判断，在 /market（市场可见）下必须找得到树节点。
    //    没有这一步，① 无法与"选择器写错了、永远数到 0"区分开。
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    await expect(
      page.getByTestId("module-side-nav").locator('[data-nav-label="全球市场"]'),
      "正对照失败：/market 的二级树没渲染出来，说明①是选择器写错而非真的没有孤儿项"
    ).toHaveCount(1);
  });
});
