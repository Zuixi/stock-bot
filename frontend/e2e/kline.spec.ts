import { test, expect as baseExpect, type Locator } from "@playwright/test";

/** K线共享组件 E2E（P4）：频率Tab/图内MA数值行/去日期轴与范围选择/结构化tooltip/复权开关。依赖运行中的 docker 栈。 */
const expect = baseExpect.configure({ timeout: 15_000 });

// antd 5 Segmented 的 radio input 为零尺寸隐藏元素（Playwright 判定 hidden），
// 控件断言与点击落在可见的 .ant-segmented-item 上；选中态以其 selected 类为准。
const segItem = (card: Locator, text: string) => card.locator(".ant-segmented-item").filter({ hasText: text });

test("个股K线：频率Tab切换、图内MA数值行可读可切换", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();

  // MA 数值行：默认 MA5/10/20 带数值（线色），MA60 灰色无名值
  //（off 态 span 文本为 "MA60 "——JSX 保留尾部空格，故 \s?）
  await expect(card.getByText(/^MA5\s\d/).first()).toBeVisible();
  await expect(card.getByText(/^MA20\s\d/).first()).toBeVisible();
  await expect(card.getByText(/^MA60\s?$/).first()).toBeVisible();

  // 点击 MA60 出现数值（切换显隐可逆）
  await card.getByText(/^MA60\s?$/).first().click();
  await expect(card.getByText(/^MA60\s\d/).first()).toBeVisible();

  // 频率 Tab：日K 默认选中，切周K 控件状态跟随
  await expect(segItem(card, "日K")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "周K").click();
  await expect(segItem(card, "周K")).toHaveClass(/ant-segmented-item-selected/);
  await segItem(card, "月K").click();
  await expect(segItem(card, "月K")).toHaveClass(/ant-segmented-item-selected/);

  // 范围选择已移除
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "1月" })).toHaveCount(0);
});

test("个股K线：tooltip 结构化展示（无可见日期轴）", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  const canvas = card.locator("canvas").first();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas not visible");
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.35);
  await page.waitForTimeout(600);
  await expect(card.getByText(/涨跌幅/)).toBeVisible();
  await expect(card.getByText(/成交量/)).toBeVisible();
  await expect(card.getByText(/成交额/)).toBeVisible();
  // 列式布局（P5）：标签逐行 + 涨跌额行
  await expect(card.getByText(/^开盘$/)).toBeVisible();
  await expect(card.getByText(/^收盘$/)).toBeVisible();
  await expect(card.getByText(/涨跌额/)).toBeVisible();
});

test("指数K线：频率Tab可用，无复权控件", async ({ page }) => {
  await page.goto("/index/000001.SH");
  const card = page.locator(".ant-card").filter({ hasText: "指数历史行情" });
  await expect(card).toBeVisible();
  await expect(card.getByText(/^MA20\s\d/).first()).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "周K" })).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "前复权" })).toHaveCount(0);
});

test("复权开关：数据就绪后可切换前复权/不复权", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: /前复权/ })).toBeVisible({ timeout: 30_000 });
  await card.locator(".ant-segmented-item").filter({ hasText: "不复权" }).click({ timeout: 30_000 });
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "不复权" })).toHaveClass(/ant-segmented-item-selected/);
  await card.locator(".ant-segmented-item").filter({ hasText: "前复权" }).click();
  await expect(card.locator(".ant-segmented-item").filter({ hasText: "前复权" })).toHaveClass(/ant-segmented-item-selected/);
});

test("个股头部：8项指标网格含今开/昨收/换手率", async ({ page }) => {
  await page.goto("/stock/600519");
  await expect(page.locator(".ant-descriptions").first()).toBeVisible();
  for (const label of ["今开", "最高", "最低", "昨收", "成交量", "成交额", "换手率", "总市值"]) {
    await expect(page.locator(".ant-descriptions-item-label").filter({ hasText: label })).toBeVisible();
  }

  // 单位口径：600519 总市值 ≈1.6万亿(>1e12)、成交额为亿级——断言形状不断言精确数
  const mcapCell = page.locator(".ant-descriptions-item").filter({ hasText: "总市值" });
  await expect(mcapCell).toContainText(/万亿/);
  const turnoverCell = page.locator(".ant-descriptions-item").filter({ hasText: "成交额" });
  await expect(turnoverCell).toContainText(/亿/);
});
