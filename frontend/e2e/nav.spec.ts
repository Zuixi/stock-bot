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

// ⚠️ **断言的判据已按 owner 裁定重设（2026-10-15）**：
// 设计整改 Phase 1 曾按 §0-A 裁定 A1 把一级导航从 Header 移入左侧图标栏，本用例当时断言
// 「1280/1440 下 10 项全部可见」。**owner 实测后否决 A1、要求保持顶栏**，故：
//   - 导航回到 Header ⇒ 10 项在 1280/1440 下**物理上放不下**（实测 @1440 折 3 项、@1280 折 4 项），
//     "10 项全部可见"这条断言的前提本身就不成立了；
//   - 但**"不许丢项"仍然是不变量** ⇒ 重设为：导航确实在顶栏 + 10 项全部渲染 +
//     放不下的那些必须能通过 AntD 的 `...` 溢出菜单**到达**。
test.describe("一级导航：在顶栏 · 10 项平铺 · 靠右 · 一个都不能丢", () => {
  for (const width of VIEWPORTS) {
    test(`@${width} 导航在 Header 内靠右 · 10 项均渲染 · 放不下的可从溢出菜单到达`, async ({ page }) => {
      // 未登录时 6 个 authenticated 项被隐藏（可见性三态），"10 项"就失去意义，
      // 故以"已登录且持 research:read"为前置——mock 会话，不碰真实鉴权。
      // 只 mock **根级 `/auth/*` 接口**，不能用 `**/auth/**`：dev server 的模块请求
      // `http://localhost:3000/src/features/auth/index.ts` 也含 `/auth/`，会被同一条路由
      // 命中并返回 JSON → 模块 MIME 错 → 整页不挂载（`primary-nav` 永远等不到）。
      // 谓词版正好能表达"只拦 `/auth/` 开头、不拦 `/src/` 下的模块"。
      await page.route(
        (url) => url.pathname.startsWith("/auth/") && !url.pathname.startsWith("/src/"),
        (route) =>
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

      // ① 导航必须在 **Header（顶栏）内** —— 把 owner 的要求机械化，防再次被搬去侧栏
      expect(
        await nav.evaluate((el) => !!el.closest("header.ant-layout-header")),
        "@" + width + " 一级导航不在顶栏内（owner 要求保持 headbar 设计）"
      ).toBe(true);

      // ② **导航靠右**（owner 2026-10-15 明确要求："headbar 这些应该靠右，而不是左"）。
      //    判据用**几何**而非 CSS 声明：导航右边界要落在顶栏宽度的右侧，且品牌与首个导航项
      //    之间必须有明显空白——后者才是"靠右"与"紧贴品牌"的区分点（只看 navRight 会漏判）。
      const geo = await page.evaluate(() => {
        const hdr = document.querySelector("header.ant-layout-header")!;
        const box = document.querySelector('[data-testid="primary-nav"]')!;
        const first = box.querySelector("[data-nav-label]")!;
        return {
          headerW: hdr.getBoundingClientRect().width,
          navRight: box.getBoundingClientRect().right,
          firstLeft: first.getBoundingClientRect().left,
          brandRight:
            hdr.querySelector(".ant-typography")?.getBoundingClientRect().right ?? 0,
        };
      });
      expect(
        geo.navRight,
        `@${width} 导航未靠右：导航右边界 ${Math.round(geo.navRight)} / 顶栏 ${Math.round(geo.headerW)}`
      ).toBeGreaterThan(geo.headerW * 0.6);
      expect(
        geo.firstLeft - geo.brandRight,
        `@${width} 导航紧贴品牌（未靠右）：品牌右缘 ${Math.round(geo.brandRight)} → 首项左缘 ${Math.round(geo.firstLeft)}`
      ).toBeGreaterThan(120);

      // ③ 10 个标签必须都渲染（防"清单改了、导航漏项"后断言静默变少）
      const hidden = [];
      for (const label of NAV_LABELS) {
        const item = nav.locator(`[data-nav-label="${label}"]`);
        await expect(item, `一级项「${label}」未渲染`).toHaveCount(1);
        // 轮询而非同步读：布局落定是异步的，全套并行（负载高）时同步断言会拿到中间态。
        // 判据一字未改（仍是 isHitTestable），只是允许它"稍后成立"。
        await expect
          .poll(() => isHitTestable(item), { message: `一级项「${label}」渲染了但不可点击` })
          .toBe(true)
          .catch(() => hidden.push(label));
      }

      // ④ 放不下的项**必须能到达**：点开 AntD 的 `...` 溢出菜单，逐个确认标签可见。
      //    没有这一步，"折进 ..."与"整项丢失"无法区分——而后者才是真缺陷。
      if (hidden.length > 0) {
        const overflow = nav.locator(".ant-menu-overflow-item-rest").first();
        await expect(overflow, `有 ${hidden.length} 项被折叠，但没有出现溢出菜单入口`).toBeVisible();
        await overflow.click();
        for (const label of hidden) {
          await expect(
            nav.locator(`[data-nav-label="${label}"]`).last(),
            `被折叠的「${label}」无法从溢出菜单到达（等于丢项）`
          ).toBeVisible();
        }
      }
    });
  }
});

// 说明：Phase 1 曾有一条「模块二级树不得出现孤儿项」的守护，用于锁住侧栏二级树的
// "只在所属一级导航可见时才渲染"这条不变量。owner 否决左侧栏后，二级树的用途已随
// `MODULE_TREES` 一起删除（`ModuleSideNav` 现由调用方传空数组 ⇒ 整块不渲染），
// 该守护失去被测对象，故删除；将来若重启三级布局，应把那条不变量一并恢复。
