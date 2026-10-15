import { test, expect as baseExpect } from "@playwright/test";

/**
 * 原始 ISO 时间戳**运行时**门禁（Phase 0.6 的运行时一半，先写先红）。
 *
 * 为什么需要它（与 `scripts/check-no-raw-iso.mjs` 的分工）：
 * 计划 §8.2 的静态扫描只看**源码字面量**。而本次评审实测的 ISO 直出不是字面量——
 * `pages/market-index/index.tsx:138` 写的是 `{index.asof}`，值由接口 payload 提供，
 * 渲染结果就是 `2026-10-13T15:00:00Z`（实测，见 task-23 报告 0.6 一节）。
 * 静态扫描对这类"值来自数据"的写法**结构上不可能看见**，于是它报绿——但那正是
 * §Why now 表里「日期四种写法」的第一种，用户确实在屏幕上读到了机器串。
 *
 * 因此本文件补上运行时一层：把页面上**可见文本**扫一遍 ISO 模式，命中即失败。
 * 两层各守各的时期，缺一层就有一类漏网。
 *
 * 断言纪律（计划 §1）：
 * - 只扫 `innerText`（**可见**文本），不扫 `innerHTML`——否则命中的可能是
 *   `title=` / `datetime=` 等机器可读属性（那反倒是正确的用法）；
 * - 锚点是**文案**（先等页面出现该页的标题/空态提示），不是固定 sleep。
 *
 * 预期红（Phase 0 完成判据）：`/market/index/000001.SH` 的「数据截至」行直出
 * `2026-10-13T15:00:00Z`。3.9（单一格式化入口 `formatAsOf`）落地后转绿。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

/** 契约模式：`\d{4}-\d{2}-\d{2}T\d{2}:\d{2}`（与 static 门禁同一模式） */
const ISO_PATTERN = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/g;

const ROUTES = ["/market/global", "/market/heatmap", "/market/index/000001.SH", "/company/600540"];

test.describe("页面可见文本不得直出原始 ISO 时间戳", () => {
  for (const route of ROUTES) {
    test(`${route} 页面文本无 ISO 串`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(route, { waitUntil: "domcontentloaded" });
      await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
      // 布局稳定（React 挂载 + 首屏请求渲染）后再读文本
      await page.waitForTimeout(1000);

      const text = await page.locator("body").innerText();
      const hits = [...new Set(text.match(ISO_PATTERN) ?? [])];

      expect(
        hits,
        `${route} 的可见文本里出现原始 ISO 时间戳：${hits.join("、")}` +
          ` —— 日期一律走 shared/ui/date.ts 的格式化入口（计划 §3.9），机器串不得上屏`
      ).toEqual([]);
    });
  }

  /**
   * 正对照（负向断言必须配一个"证明它抓得住"的对照）：
   *
   * 上一条只断言"没有 ISO"——若页面走未找到分支、或 `asof` 恰好为空，它同样会绿。
   * 故这一条钉住**修复后的形态**：`数据截至` 行必须由 `formatAsOf` 渲染
   * （`M月D日收盘`，§3.9 的契约），而不是别的什么把它藏起来的写法。
   * 修复前本页渲染的是 `2026-10-13T15:00:00Z` ⇒ 这一条当时也必然是红的。
   */
  test("指数详情「数据截至」走 formatAsOf（收盘口径），不是机器串", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/market/index/000001.SH", { waitUntil: "domcontentloaded" });
    await page.waitForLoadState("networkidle", { timeout: 10_000 }).catch(() => {});
    await page.waitForTimeout(1000);

    const item = page.locator(".ant-descriptions-item").filter({ hasText: "数据截至" });
    if ((await item.count()) === 0) {
      // 不是"没找到就静默跳过"：没有口径行时页面必须**自证**它是"找不到该指数"，
      // 而不是把日期藏起来（否则这条断言可以被"删掉渲染"轻松弄绿 —— 假绿）。
      await expect(page.getByText("未找到该指数")).toBeVisible();
      test.skip(true, "上证指数当前无行情（页面走「未找到该指数」分支），本页无从断言口径形态");
    }
    await expect(item.first()).toContainText(/\d+月\d+日收盘/);
    await expect(item.first()).not.toContainText(/T\d{2}:\d{2}/);
  });
});
