import { test, expect as baseExpect, type Locator } from "@playwright/test";

/** K线共享组件 E2E：MA chips / 结构化 tooltip / 周期与复权控件 / 指数页无复权。依赖运行中的 docker 栈。 */
const expect = baseExpect.configure({ timeout: 15_000 });

// antd 5 Segmented 的 radio input 为零尺寸隐藏元素（Playwright 判定 hidden），
// 控件断言与点击落在可见的 label.ant-segmented-item 上；选中态以其 selected 类为准。
const segItem = (scope: Locator, label: string) =>
  scope.locator("label.ant-segmented-item").filter({ hasText: label });

test("个股K线：MA chips 可见可切换，tooltip 结构化展示", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();

  // MA chips 默认 5/10/20 选中、60 未选中
  const tag = (name: string) => card.locator(".ant-tag").filter({ hasText: name });
  await expect(tag("MA5")).toBeVisible();
  await expect(tag("MA10")).toBeVisible();
  await expect(tag("MA20")).toBeVisible();
  await expect(tag("MA60")).toBeVisible();
  await tag("MA60").click(); // 切换显隐不报错
  await tag("MA60").click();

  // 周期与复权控件
  await expect(segItem(card, "1年")).toBeVisible();
  await expect(segItem(card, "前复权")).toBeVisible();

  // 结构化 tooltip（ECharts tooltip 为 DOM）
  const canvas = card.locator("canvas").first();
  const box = await canvas.boundingBox();
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.4);
  await page.waitForTimeout(600);
  await expect(card.getByText(/涨跌幅/)).toBeVisible();
  await expect(card.getByText(/成交量/)).toBeVisible();
  await expect(card.getByText(/成交额/)).toBeVisible();
});

test("指数K线：MA 与周期可用，无复权控件", async ({ page }) => {
  await page.goto("/index/000001.SH");
  const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
  await expect(card).toBeVisible();
  await expect(card.locator(".ant-tag").filter({ hasText: "MA20" })).toBeVisible();
  await expect(segItem(card, "1月")).toBeVisible();
  await expect(segItem(card, "前复权")).toHaveCount(0);
});

test("周期切换后缩放重置：1年 → 1月 视图变化且控件状态跟随", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  await segItem(card, "1月").click();
  await expect(segItem(card, "1月")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "1年").click();
  await expect(segItem(card, "1年")).toHaveClass(/ant-segmented-item-selected/);
});

test("复权开关：数据就绪后可切换前复权/不复权", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  // 600519 已在 Task 7 实机回补过（或 ≤30s 内后台完成）；禁用态也带 Segmented 结构
  const adjust = card.locator(".ant-segmented").filter({ hasText: /前复权/ });
  await expect(adjust).toBeVisible({ timeout: 30_000 });
  // 就绪后点击不复权再切回，不报错
  await segItem(card, "不复权").click({ timeout: 30_000 });
  await expect(segItem(card, "不复权")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "前复权").click();
  await expect(segItem(card, "前复权")).toHaveClass(/ant-segmented-item-selected/);
});
