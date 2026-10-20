import { expect, test } from "@playwright/test";

/**
 * A股全景 · 市场结构脉搏（Stage 1）
 *
 * 只断言结构与行为，不锁死一天的具体数字：真实的 `/market/panorama` 是日表读面，
 * 数字会随交易日变化；这里锁的是"有状态、有 4 个 KPI、罗盘在、切范围不跳、
 * 移动端不溢出、涨跌分布/行业网格接上了变化"。
 */

const PULSE = '[data-testid="market-panorama"]';
const STATES = /普涨|结构|普跌|抱团|数据不足/;

test.describe("A股全景 · 市场结构脉搏", () => {
  test("1440：状态、罗盘、4 个 KPI、日表口径可见", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    const pulse = page.locator(PULSE);
    await expect(pulse).toBeVisible({ timeout: 15_000 });

    await expect(pulse.locator(".pano__state")).toHaveText(STATES);
    await expect(pulse.locator(".pano-kpi")).toHaveCount(4);
    await expect(pulse.locator("[data-testid='market-panorama-compass'] canvas")).toHaveCount(1);
    await expect(pulse.locator(".pano__asof")).toContainText(/数据截至|—/);
    await expect(pulse).toContainText("广度 × 集中度");
    await expect(pulse).toContainText("行业归属：按当前申万快照回溯");

    // 涨跌分布卡接上了"较昨日"与 5 日上涨占比
    await expect(page.locator(".dist-change")).toContainText("较昨日");
    await expect(page.locator(".dist-change")).toContainText("5日上涨占比");

    // 行业网格有今日涨跌角标（不是只有成分数）
    await expect(page.locator(".sw-pct").first()).toBeVisible();
  });

  test("切 250D：URL 生效、不闪骨架、不产生页面跳动", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/market?tab=panorama&pano=60", { waitUntil: "domcontentloaded" });
    const pulse = page.locator(PULSE);
    await expect(pulse).toBeVisible({ timeout: 15_000 });
    // 等首帧数据落位再量：脉搏 skeleton 与数据态高度应相同，但"量在请求中"会把
    // 网络抖动误判成布局跳动。
    await expect(pulse.locator(".pano-kpi")).toHaveCount(4);
    await expect(pulse.locator(".pano__state")).not.toHaveText("—");
    await page.waitForFunction(() => document.body.scrollHeight > 1200);
    // 顶部 Tabs/页头在首帧后还有一次布局稳定（字体/ink bar）；等它落位再取基线，
    // 否则会把"页头从 55px 长到 80px"误判成脉搏位移。
    await page.waitForTimeout(500);

    // 只量脉搏自身：body 里还有别的分区在异步加载（拉长 body 的不是本组件）。
    const before = await page.evaluate(() => {
      const r = document.querySelector('[data-testid="market-panorama"]')!.getBoundingClientRect();
      return { y: window.scrollY, h: r.height, top: r.top + window.scrollY };
    });

    const response = page.waitForResponse(
      (r) => r.url().includes("/api/v1/market/panorama") && r.url().includes("days=250")
    );
    await pulse.locator(".ant-segmented-item-label", { hasText: "250D" }).click();
    await response;
    await expect(page).toHaveURL(/pano=250/);
    await expect(pulse.locator(".pano__asof")).toBeVisible();
    await expect(pulse.locator(".ant-skeleton")).toHaveCount(0);

    // 等 React Query 的 keepPreviousData 完成后，滚动位置与文档高度不应变化
    await page.waitForTimeout(500);
    const after = await page.evaluate(() => {
      const r = document.querySelector('[data-testid="market-panorama"]')!.getBoundingClientRect();
      return { y: window.scrollY, h: r.height, top: r.top + window.scrollY };
    });
    expect(after.y, `切范围后 scrollY 变了：${before.y} → ${after.y}`).toBe(before.y);
    expect(
      Math.abs(after.top - before.top),
      `切范围后脉搏上沿位移：${before.top} → ${after.top}`
    ).toBeLessThanOrEqual(1);
    expect(
      Math.abs(after.h - before.h),
      `切范围后脉搏高度：${before.h} → ${after.h}（keepPreviousData/固定高度失效）`
    ).toBeLessThanOrEqual(2);
  });

  test("热力图可回看上一交易日：昨日/今日切换带 date 参数", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 1000 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    const pulse = page.locator(PULSE);
    await expect(pulse).toBeVisible({ timeout: 15_000 });
    const card = page.locator(".ant-card").filter({ hasText: "板块热力图" });
    const prev = card.locator(".ant-segmented-item-label", { hasText: "昨日" });
    await expect(prev).toBeVisible({ timeout: 10_000 });

    // ⚠️ 日期**不得写死**（2026-10-19 修）：本用例原来断言「10月15日 / 10月16日」，一旦真实
    // 数据推进到下一个交易日就假红。改为从两个读面取**当天真实**的日期：
    //   今日 = `/market/sectors` 的 `as_of`；昨日 = `/market/panorama` 的 `breadth.previous.trade_date`。
    const { today, prevDay } = await page.evaluate(async () => {
      const [sectorsRes, panoRes] = await Promise.all([
        fetch("/api/v1/market/sectors", { credentials: "include" }),
        fetch("/api/v1/market/panorama?days=20", { credentials: "include" }),
      ]);
      const sectors = sectorsRes.ok ? await sectorsRes.json() : null;
      const pano = panoRes.ok ? await panoRes.json() : null;
      return {
        today: sectors?.as_of ?? null,
        prevDay: pano?.breadth?.previous?.trade_date ?? null,
      };
    });
    expect(today, "热力图需要一个真实的最新交易日（/market/sectors 的 as_of）").toBeTruthy();
    expect(prevDay, "「昨日」必须有可回看的上一交易日（panorama.breadth.previous）").toBeTruthy();

    const cn = (iso: string) => {
      const [, m, d] = iso.split("-");
      return `${Number(m)}月${Number(d)}日`;
    };

    const response = page.waitForResponse(
      (r) => r.url().includes("/api/v1/market/sectors") && r.url().includes(`date=${prevDay}`)
    );
    await prev.click();
    await response;
    await expect(card.getByText(new RegExp(cn(prevDay as string))).first()).toBeVisible();

    await card.locator(".ant-segmented-item-label", { hasText: "今日" }).click();
    await expect(card.getByText(new RegExp(cn(today as string))).first()).toBeVisible();
  });

  test("390：罗盘与 4 个 KPI 可读，无横向溢出", async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/market?tab=panorama", { waitUntil: "domcontentloaded" });
    const pulse = page.locator(PULSE);
    await expect(pulse).toBeVisible({ timeout: 15_000 });
    await expect(pulse.locator(".pano-kpi")).toHaveCount(4);
    await expect(pulse.locator("[data-testid='market-panorama-compass'] canvas")).toHaveCount(1);
    const overflow = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth
    );
    expect(overflow, `移动端横向溢出 ${overflow}px`).toBeLessThanOrEqual(0);
  });
});
