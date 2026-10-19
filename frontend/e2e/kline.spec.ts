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
  // P1-1 起卡片内多了一行「日期 开 高 低 收 涨跌幅 量 额 + MA」的 ribbon（图上方），
  // 它和 tooltip 会同时出现「涨跌幅/成交量/成交额」这些同名标签 ⇒ 不 scope 就撞
  // strict-mode（一个 locator 解析到 2 个元素）。`.kline-tooltip` 是 option 里显式
  // 声明的类名（见 `klineOption.ts` 的 `tooltip.className`），专为这种读数断言而留。
  const tooltip = card.locator(".kline-tooltip");
  await expect(tooltip.getByText(/涨跌幅/)).toBeVisible();
  await expect(tooltip.getByText(/成交量/)).toBeVisible();
  await expect(tooltip.getByText(/成交额/)).toBeVisible();
  // 列式布局（P5）：标签逐行 + 涨跌额行
  await expect(tooltip.getByText(/^开盘$/)).toBeVisible();
  await expect(tooltip.getByText(/^收盘$/)).toBeVisible();
  await expect(tooltip.getByText(/涨跌额/)).toBeVisible();
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
  // 11 行完整内容：成交量/成交额这两个「只有 bar 侧才在读」的行 + 需回查 points 的 OHLC。
  // scope 到 `.kline-tooltip`：图上方 ribbon 也有同名标签（见上一个用例的注释）。
  const tooltip = card.locator(".kline-tooltip");
  await expect(tooltip.getByText(/成交量/)).toBeVisible();
  await expect(tooltip.getByText(/成交额/)).toBeVisible();
  await expect(tooltip.getByText(/涨跌幅/)).toBeVisible();
  await expect(tooltip.getByText(/^开盘$/)).toBeVisible();
  await expect(tooltip.getByText(/^收盘$/)).toBeVisible();
});

test("个股K线：ribbon 日期随悬停变化，移出画布回落最后一根", async ({ page }) => {
  await page.goto("/stock/600519");
  const card = page.locator(".ant-card").filter({ hasText: "历史行情" });
  await expect(card).toBeVisible();
  // 与 tooltip 用例同一个等待信号：MA 行带数值 = ECharts 已拿到数据（否则悬停空 canvas）
  await expect(card.getByText(/MA5 \d/)).toBeVisible();
  const ribbon = card.getByTestId("kline-ribbon");
  await expect(ribbon).toBeVisible();
  const ribbonDate = card.getByTestId("kline-ribbon-date");
  // 未悬停时 ribbon 显示**最后一根**：先把这个日期记下来当对照
  const lastDate = (await ribbonDate.innerText()).trim();
  expect(lastDate.length).toBeGreaterThan(0);

  const canvas = card.locator("canvas").first();
  // 同前两个用例：先滚进视口再算坐标，否则 mouse.move 的 y 落在视口之外
  await canvas.scrollIntoViewIfNeeded();
  const box = await canvas.boundingBox();
  if (!box) throw new Error("canvas not visible");
  // 70% 宽落在默认视窗（最近 120 根）的中后段，不可能是最后一根
  await page.mouse.move(box.x + box.width * 0.7, box.y + box.height * 0.35);
  // web-first 断言自带重试：rAF 节流 + 事件派发完成后 ribbon 才会换文本
  await expect(ribbonDate).not.toHaveText(lastDate);

  // ⚠️ 只断言"日期变了"**不够**：ribbon 换成相邻一根（off-by-one）、或把 y 轴的价格
  // 当成下标再夹进序列，都能让上一条通过。这里把三方钉在一起 —— tooltip 的日期头来自
  // ECharts 的 `params[0].dataIndex`（光标真正命中的那根），ribbon 的日期来自
  // `updateAxisPointer` 解析出的下标；两者逐字相等，才证明 ribbon ↔ 悬停 bar 的映射正确。
  // `.kline-tooltip__date` 是 `klineOption.ts` formatter 里显式声明的类名。
  const tooltipDate = card.locator(".kline-tooltip__date");
  await expect(tooltipDate).toBeVisible();
  const hoveredDate = (await tooltipDate.innerText()).trim();
  expect(hoveredDate, "tooltip 日期头应是 ISO 日期（否则下面拿它当 oracle 没有意义）").toMatch(
    /^\d{4}-\d{2}-\d{2}$/,
  );
  await expect(ribbonDate, "ribbon 日期必须等于 tooltip 那一根（同一根悬停 bar）").toHaveText(
    hoveredDate,
  );

  // 验收项「鼠标移出恢复最后一根」：`globalout` / axisPointer leave 两条路都写回 null
  await page.mouse.move(10, 10);
  await expect(ribbonDate).toHaveText(lastDate);

  // `<768` 不渲染 ribbon（计划 §5：两行 DOM 会吃掉 320px 画布里的 60px，
  // 窄屏的悬停信息回落到 tooltip）——同一份卡片，改变视口后 ribbon 必须消失
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(card.getByTestId("kline-ribbon")).toHaveCount(0);
  // MA 行在窄屏仍然保留（窄屏不是"藏图"，§4.4）
  await expect(card.getByTestId("kline-ma-legend")).toBeVisible();
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
