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
  // 与其余 e2e mock（auth/sentiment/signalPanel/userIsolation）同址：缺这个字段会让
  // `hasPermission` 的 `user.is_superuser` 分支读到 `undefined`（今天恰好等价 false，
  // 但那是"靠 undefined 恰好为假"的运气，不是契约）。
  is_superuser: false,
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

// ⚠️ **本用例的判据演进过两次，读之前先看这段历史**：
//   ① 设计整改 Phase 1 按 §0-A 裁定 A1 把导航移入左侧栏，当时断言「10 项全部可见」；
//   ② owner 实测后否决 A1、要求保持顶栏（`28d4ed3`）⇒ 10 项平铺在顶栏放不下，
//      断言改为「在顶栏 + 不丢项 + 可从 ... 到达」；
//   ③ owner 批准对标竞品（FinScope）把顶栏收敛为「4 平铺 + 2 分组」⇒ 顶层不再溢出，
//      故本用例现在断言**更强的**不变量：**1280/1440 下顶层不得出现 `...`**（这正是收敛的目的）。
test.describe("一级导航：在顶栏 · 4 平铺 + 2 分组 · 顶层不溢出 · 10 项一个不丢", () => {
  /** 顶层直接可见的 4 项 */
  const FLAT = ["市场", "资讯", "行业分析", "公司分析"];
  /** 分组 → 组内目的地（与 `MainLayout.tsx` 的 `NAV_GROUPS` 一致） */
  const GROUPS: { title: string; items: string[] }[] = [
    { title: "研究", items: ["宏观分析", "情绪分析", "价值网图谱"] },
    { title: "工具", items: ["策略引擎", "风险管理", "交易执行"] },
  ];

  for (const width of VIEWPORTS) {
    test(`@${width} 顶栏 4 项直出 · 2 分组可展开 · 顶层无 ... 溢出`, async ({ page }) => {
      // 未登录时 6 个 authenticated/permission 项被隐藏，"10 项"就失去意义，
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

      // ① 导航必须在 **Header（顶栏）内** —— 把 owner 的要求机械化，防再次被搬去侧栏
      expect(
        await nav.evaluate((el) => !!el.closest("header.ant-layout-header")),
        `@${width} 一级导航不在顶栏内（owner 要求保持 headbar 设计）`
      ).toBe(true);

      // ② **顶层不得出现 `...` 溢出** —— 这是"4 平铺 + 2 分组"收敛的目的。
      //    用结构选择器而不是截图比对：AntD 的溢出项有稳定的类名。
      await expect(
        nav.locator(".ant-menu-overflow-item-rest"),
        `@${width} 顶层仍出现「...」溢出（收敛未达成）`
      ).toHaveCount(0);

      // ③ 4 个平铺项直出且**真的能被点到**（命中测试，不用 toBeVisible——
      //    它不计祖先 overflow 裁剪，会把折叠项误报为可见）
      for (const label of FLAT) {
        const item = nav.locator(`[data-nav-label="${label}"]`);
        await expect(item, `平铺项「${label}」未渲染`).toHaveCount(1);
        // 用**轮询**断言"可点击"，不用同步 `expect(await ...)`：
        // 布局/ResizeObserver 的落定是异步的，全套并行（负载高）时同步断言会读到中间态。
        // 这不是放宽——`isHitTestable` 的判据一字未改，只是允许它"稍后成立"。
        await expect
          .poll(() => isHitTestable(item), { message: `平铺项「${label}」不可点击` })
          .toBe(true);
      }

      // ④ 2 个分组：展开后组内每一项都必须可点击（**这是"一个不丢"的判据**）
      for (const group of GROUPS) {
        const title = nav.locator(".ant-menu-submenu-title", { hasText: group.title }).first();
        await expect(title, `分组「${group.title}」未渲染`).toHaveCount(1);
        await title.click();
        for (const label of group.items) {
          const item = page.locator(`[data-nav-label="${label}"]`).last();
          await expect(item, `分组「${group.title}」里的「${label}」未渲染`).toHaveCount(1);
          // 同上：下拉弹层有开启动画与定位过程，必须轮询而非同步断言
          await expect
            .poll(() => isHitTestable(item), { message: `「${label}」展开后仍不可点击` })
            .toBe(true);
        }
        await page.keyboard.press("Escape"); // 收起，避免遮挡下一个分组
      }
    });
  }

  // 权限边界：**分组里若一个子项都不可见，整个分组不得渲染**——
  // 否则会留下一个"点开是空的"下拉，那是比"看不见入口"更糟的假入口。
  //
  // ⚠️ **本条断言修正于 2026-10-15（P1-1 任务实测）**，原文是
  // `无 research:read ⇒ 「研究」分组 toHaveCount(0)`，前提写的是
  // 「研究分组三个子项全部不可见」。该前提与 `MainLayout.tsx` 的 `NAV_PRESENTATION`
  // **不符**：`/macro`（宏观分析）与 `/valuegraph`（价值网图谱）是 `authenticated`，
  // 只有 `/sentiment`（情绪分析）要 `research:read`。于是"去掉 research:read"**永远不会**
  // 让整组消失 —— 组里还有两个已登录可见的子项。
  //
  // 原文之所以平时是绿的，是因为它在**会话未解析**的窗口里跑（`user === null` ⇒ `visible()`
  // 全假 ⇒ 整组不渲染 ⇒ 空断言恰好为真）。这一步是**竞态**：全量套件里实测**必红**
  // （连续 3 次全量运行都红在这一条），单跑这 1 条必绿（412ms）。红的原因与页面/市场页无关，
  // 是断言自己站不住。
  //
  // 改为直接断言**真实不变量**（不依赖会话何时落地）：无权时
  //   ① 「研究」分组仍在，但**组内恰好只剩两个 `authenticated` 子项**、权限子项「情绪分析」消失
  //      —— 这正是"不得渲染空下拉"要守的事实（组非空），且把"哪个子项该消失"钉死；
  //   ② 「工具」分组同样在（正对照：选择器没写错）。
  // 为消除竞态，断言前显式等"导航已按会话终态渲染"（出现 `authenticated` 子项「资讯」）。
  test("@1280 无 research:read 时「研究」分组只漏掉权限子项、不渲染为空下拉", async ({ page }) => {
    await page.route("**/auth/**", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          route.request().url().includes("/session")
            ? { ...MOCK_USER, permissions: ["stocks:read"] } // 故意不给 research:read
            : { ok: true }
        ),
      })
    );
    await page.setViewportSize({ width: 1280, height: 900 });
    await page.goto("/market", { waitUntil: "domcontentloaded" });

    const nav = page.getByTestId("primary-nav");
    // 先等"导航确实按会话终态渲染过了"：`资讯` 是 `authenticated` 门禁的平铺项，
    // 它在未登录态**不渲染**。等它出现 = 会话已解析完，后续断言不再与加载赛跑。
    await expect(
      nav.locator('[data-nav-label="资讯"]'),
      "导航未按已登录态渲染（会话未解析）—— 断言会退化成空转"
    ).toHaveCount(1);

    const research = nav.locator(".ant-menu-submenu-title", { hasText: "研究" });
    await expect(research, "「研究」分组整个不见了（组内仍有 authenticated 子项，不该消失）").toHaveCount(1);
    // 正对照：同一套选择器必须能找到仍然可见的「工具」分组，证明上一条不是"选择器写错"
    await expect(
      nav.locator(".ant-menu-submenu-title", { hasText: "工具" }),
      "正对照失败：连「工具」分组都找不到，说明上一条是选择器问题而非真的没有空下拉"
    ).toHaveCount(1);

    // 展开分组：子项**用 `page` 作用域**（与本文件上面 GROUPS 循环里的写法逐字一致）——
    // 横向 Menu 的子菜单挂在 portal（`.ant-menu-submenu-popup`）里，**不在** `primary-nav` 内，
    // 故 `nav.locator("...")` 恒为 0（那会写出一条永远红的假断言）。
    // 判据是**按名字点名**：两个 `authenticated` 子项必须在、权限子项必须不在 ——
    // 两条合起来正是"组非空且过滤正确"。
    await research.click();
    await expect(
      page.locator('[data-nav-label="宏观分析"]').last(),
      "无 research:read 不应影响 authenticated 子项「宏观分析」（组被展开成空下拉 = 假入口）"
    ).toHaveCount(1);
    await expect(
      page.locator('[data-nav-label="价值网图谱"]').last(),
      "无 research:read 不应影响 authenticated 子项「价值网图谱」"
    ).toHaveCount(1);
    await expect(
      page.locator('[data-nav-label="情绪分析"]'),
      "无 research:read 时「情绪分析」仍渲染（点进去只会撞 403）"
    ).toHaveCount(0);
  });
});

// 说明：Phase 1 曾有一条「模块二级树不得出现孤儿项」的守护，用于锁住侧栏二级树的
// "只在所属一级导航可见时才渲染"这条不变量。owner 否决左侧栏后，二级树的用途已随
// `MODULE_TREES` 一起删除（`ModuleSideNav` 现由调用方传空数组 ⇒ 整块不渲染），
// 该守护失去被测对象，故删除；将来若重启三级布局，应把那条不变量一并恢复。
