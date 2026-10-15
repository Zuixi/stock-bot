import { test, expect } from "@playwright/test";

/**
 * 前端重新部署 → 旧哈希懒加载分块 404 的恢复通道 E2E（Task 20）。
 *
 * 对应实现：`src/app/chunk-error-recovery.ts`（`main.tsx` 首行 import 自注册）。
 * 本仓库无前端单测运行器（无 vitest/jest），因此真实浏览器断言是**唯一**能驱动守卫决策的
 * 验证手段：纯函数 `shouldReload` / `markReloaded` 的判定必须落到真实 sessionStorage 与
 * 真实事件里 —— 否则守卫等于没被验证过。
 *
 * 断言口径（都是实测踩过的坑，写下来免得后人再踩）：
 *   - 凡是"失败 → 自动刷新"的用例，**不能**在派发后断言 toast 可见：刷新会销毁页面，
 *     Playwright 的可见性断言以 "waiting for navigation to finish" 失败 —— 成功路径被
 *     断言方式打死（本 spec 初版就是这么假红的）。这类用例只断言刷新真的发生
 *     （`performance.timeOrigin` 变大 = 换了文档）与守卫标记真的落盘。
 *   - 要观察 toast 文案，用 `DISABLE_AUTO_RELOAD` 关掉自动刷新后再触发：没有导航撕页面，
 *     locator 断言才是稳定的。
 *   - 导航后所有 `page.evaluate` 都可能抛 "Execution context was destroyed"，
 *     轮询必须容忍它：上下文被销毁本身正是"确实刷新了"的证据。
 *   - 不能给 `location.reload` 装桩：Chromium 下它既不可写也不可重新配置
 *     （`Object.defineProperty` 抛 "Cannot redefine property: reload"）。
 */

/**
 * 只放行 vite 自己的模块请求，其余业务接口全部本地短路（不碰真实后端/DB）。
 * 必须用**锚定到路径根**的正则：glob 形式的 "**\/api/**" 会连 /node_modules/.vite/deps/ 里
 * 带 "api" 字样的模块一起吞掉，把模块请求回成 JSON，vite 直接拒载 → #root 永远空，测试假红。
 */
const BACKEND_API = /^https?:\/\/[^/]+\/(api|auth|\.well-known)\//;

const FLAG_KEY = "stockbot:chunk-reloaded";
const MANUAL_NOTICE = /请手动刷新/;

const DISABLE_AUTO_RELOAD = () => window.sessionStorage.setItem("stockbot:chunk-reloaded", "1");

async function stubBackend(page: import("@playwright/test").Page) {
  await page.route(BACKEND_API, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: "{}" })
  );
}

/** 等应用挂载完成 = 恢复通道必然已注册（它在 main.tsx 里早于 ReactDOM.createRoot）。 */
async function gotoAppMounted(page: import("@playwright/test").Page) {
  await page.goto("/");
  await expect(page.locator("#root")).not.toBeEmpty();
}

const sessionFlag = (page: import("@playwright/test").Page) =>
  page.evaluate((key) => window.sessionStorage.getItem(key), FLAG_KEY);

const navTimeOrigin = (page: import("@playwright/test").Page) =>
  page.evaluate(() => performance.timeOrigin);

/** 恢复通道会**主动导航**：等待过程中必须容忍执行上下文被销毁。 */
async function pollWithRetry<T>(
  page: import("@playwright/test").Page,
  read: () => Promise<T>,
  timeoutMs = 15_000
): Promise<T> {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      return await read();
    } catch (error) {
      if (Date.now() >= deadline) throw error;
      await page.waitForTimeout(50);
    }
  }
}

/** 等恢复通道真的换了文档（`performance.timeOrigin` 变大 = 发生了一次新导航）。 */
async function waitForNavigationAfter(page: import("@playwright/test").Page, before: number) {
  await expect
    .poll(() => pollWithRetry(page, () => navTimeOrigin(page)), { timeout: 15_000 })
    .toBeGreaterThan(before);
}

const dispatchPreloadError = (page: import("@playwright/test").Page) =>
  page.evaluate(() => {
    window.dispatchEvent(new Event("vite:preloadError", { cancelable: true }));
  });

const dispatchDynamicImportRejection = (page: import("@playwright/test").Page) =>
  page.evaluate(() => {
    const promise = Promise.reject(new Error("swallowed")).catch(() => undefined);
    window.dispatchEvent(
      new PromiseRejectionEvent("unhandledrejection", {
        cancelable: true,
        promise,
        reason: new TypeError(
          "Failed to fetch dynamically imported module: http://127.0.0.1:3000/assets/index-iB5Al3gl.js"
        ),
      })
    );
  });

test.describe("分块失效恢复通道", () => {
  test("自动路径：本会话首次失败 → 落守卫标记 → 真的自动刷新一次", async ({ page }) => {
    await stubBackend(page);
    await gotoAppMounted(page);
    const before = await navTimeOrigin(page);

    await dispatchPreloadError(page);

    // 真刷新 = 换了新文档；由恢复通道自己触发，不是测试显式 reload
    await waitForNavigationAfter(page, before);
    // 守卫标记在新文档里仍在 → 同会话第二次失败不会再自动刷新
    expect(await pollWithRetry(page, () => sessionFlag(page))).toBe("1");
    await expect(page.locator("#root")).not.toBeEmpty();
  });

  test("提示文案：失败会以中文提示告知用户（antd message 通道 + 常驻手动提示）", async ({ page }) => {
    await stubBackend(page);
    // 关掉自动刷新：toast 渲染出来之前就被导航撕掉的话，locator 断言永远抓不到。
    // 这里预置守卫标记，走的是"已刷过一次"的分支（文案 `请手动刷新…`），
    // 证明提示通道可用；"正在刷新…"那条分支由下面的自动路径用例覆盖行为。
    await page.addInitScript(DISABLE_AUTO_RELOAD);
    await gotoAppMounted(page);

    await dispatchPreloadError(page);

    await expect(page.getByText(MANUAL_NOTICE)).toBeVisible();
    // duration 0 = 常驻，3 秒后仍在（否则用户根本看不到）
    await page.waitForTimeout(3_500);
    await expect(page.getByText(MANUAL_NOTICE)).toBeVisible();
    expect(await sessionFlag(page)).toBe("1");
  });

  test("防重入：自动刷新后的第二次失败，不再刷新并改为常驻「请手动刷新」提示", async ({ page }) => {
    await stubBackend(page);
    await gotoAppMounted(page);
    const before = await navTimeOrigin(page);

    await dispatchPreloadError(page);
    await waitForNavigationAfter(page, before);
    await expect(page.locator("#root")).not.toBeEmpty();
    const afterReload = await pollWithRetry(page, () => navTimeOrigin(page));
    expect(await pollWithRetry(page, () => sessionFlag(page))).toBe("1");

    // 第二次失败：守卫必须挡住自动刷新（否则就是无限刷新环）
    await dispatchPreloadError(page);
    await expect(page.getByText(MANUAL_NOTICE)).toBeVisible();
    expect(await pollWithRetry(page, () => navTimeOrigin(page))).toBe(afterReload);
    expect(await pollWithRetry(page, () => sessionFlag(page))).toBe("1");
  });

  test("同会话第二次失败：不再自动刷新，改为常驻提示", async ({ page }) => {
    await stubBackend(page);
    // 模拟"上一次失败已经自动刷新过"（或用户手动刷过又旧了）：守卫标记已存在
    await page.addInitScript((key) => window.sessionStorage.setItem(key, "1"), FLAG_KEY);
    await gotoAppMounted(page);

    const timeOrigin = await navTimeOrigin(page);
    await dispatchPreloadError(page);

    await expect(page.getByText(MANUAL_NOTICE)).toBeVisible();
    // 核心断言：没有发生自动刷新 —— 不可能成环
    expect(await pollWithRetry(page, () => navTimeOrigin(page))).toBe(timeOrigin);
  });

  test("兜底路径：动态 import 失败（unhandledrejection）同样被接住并恢复", async ({ page }) => {
    await stubBackend(page);
    await gotoAppMounted(page);
    const before = await navTimeOrigin(page);

    await dispatchDynamicImportRejection(page);
    // 这条路径没有权威事件，靠错误文案匹配；匹配成功同样应"落标记 + 刷新"
    await waitForNavigationAfter(page, before);
    expect(await pollWithRetry(page, () => sessionFlag(page))).toBe("1");
  });

  test("噪声不误伤：后端 5xx 之类的普通 rejection 不触发刷新", async ({ page }) => {
    await stubBackend(page);
    await gotoAppMounted(page);
    const timeOrigin = await navTimeOrigin(page);

    await page.evaluate(() => {
      const promise = Promise.reject(new Error("swallowed")).catch(() => undefined);
      window.dispatchEvent(
        new PromiseRejectionEvent("unhandledrejection", {
          cancelable: true,
          promise,
          reason: new Error("Request failed with status code 500"),
        })
      );
    });

    await page.waitForTimeout(500);
    expect(await navTimeOrigin(page)).toBe(timeOrigin);
    expect(await sessionFlag(page)).toBeNull();
    await expect(page.getByText(MANUAL_NOTICE)).toHaveCount(0);
  });
});
