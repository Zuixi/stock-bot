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
  // ⚠️ 等到**图表真的拿到数据**再悬停：卡片"可见"只说明容器挂上了，ECharts 的 canvas
  // 可能还没有数据点 ⇒ 悬停不出 tooltip ⇒ 下面断言失败。全量跑（请求竞争激烈）时必现、
  // 单跑常过，是典型时序 flake。MA 图例带数值 = 数据已渲染，是可判定的等待信号。
  await expect(card.getByText(/MA5 \d/)).toBeVisible();
  const canvas = card.locator("canvas").first();
  // ⚠️ 先把画布滚进视口再算坐标：1280x720 下画布顶边约在 y=585、高 400，
  // `box.y + height*0.35 ≈ 725` **落在视口之外** ⇒ mouse.move 到不了画布 ⇒ 没有 tooltip。
  // （1440x900 时不越界，所以这个 bug 只在特定视口/页面高度下出现，表现为"时好时坏"。）
  await canvas.scrollIntoViewIfNeeded();
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

test("个股K线：悬停成交量 pane 仍出完整 tooltip（跨 pane 联动）", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  // 同样的等待策略（见上一个用例的注释）：MA 图例带数值 = ECharts 已拿到数据，
  // 否则悬停在空 canvas 上不会有任何 tooltip，全量跑时必 flake。
  await expect(card.getByText(/MA5 \d/)).toBeVisible();
  const canvas = card.locator("canvas").first();
  // 先滚进视口再算坐标（同上一个用例的注释）。
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas not visible");
  // y = 78% 落在成交量 pane 内：grid[1] 是 `top: "68%", height: "16%"` ⇒ 68%–84%。
  // ECharts 的 axis tooltip 只收集**当前悬停 axis 上**的 series，该 pane 只有那条
  // `成交量` bar，没有 candlestick ⇒ formatter 若只认 candlestick 就返回空串（F5）。
  await page.mouse.move(box.x + box.width * 0.75, box.y + box.height * 0.78);
  await page.waitForTimeout(600);
  // 11 行完整内容：成交量/成交额这两个「只有 bar 侧才在读」的行 + 需回查 points 的 OHLC
  await expect(card.getByText(/成交量/)).toBeVisible();
  await expect(card.getByText(/成交额/)).toBeVisible();
  await expect(card.getByText(/涨跌幅/)).toBeVisible();
  await expect(card.getByText(/^开盘$/)).toBeVisible();
  await expect(card.getByText(/^收盘$/)).toBeVisible();
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
  // P1-3：头部由 antd `<Descriptions>` 换成报价条（`.quote-bar`）。锚点改用 data-* 语义选择器
  // （`data-testid` / `data-metric`），不再依赖 AntD 的内部类名 —— 否则下一次改版又会红。
  const bar = page.getByTestId("quote-bar");
  await expect(bar).toBeVisible();
  for (const label of ["今开", "最高", "最低", "昨收", "成交量", "成交额", "换手率", "总市值"]) {
    await expect(bar.locator(`[data-metric="${label}"]`)).toBeVisible();
  }

  // 单位口径：600519 总市值 ≈1.6万亿(>1e12)、成交额为亿级——断言形状不断言精确数
  await expect(bar.locator('[data-metric="总市值"]')).toContainText(/万亿/);
  await expect(bar.locator('[data-metric="成交额"]')).toContainText(/亿/);

  // "数据截至"绑定最新行情 trade_date（全站统一的「9月16日」口径），而不是
  // 名录 asof —— 后者是 ingest 时间戳，会渲染成 2026-05-08T15:09:16.546850Z
  await expect(page.getByTestId("quote-asof")).toContainText(/^数据截至 \d+月\d+日$/);
});
