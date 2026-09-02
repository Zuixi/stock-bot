import { test, expect as baseExpect } from "@playwright/test";

/**
 * 投研工作台浏览器级 E2E（依赖运行中的 docker 栈，数据为实盘/混合源，故断言只锚定结构与中文标签，不锚定具体数值）。
 * - /research：行业卡片（生猪养殖）+ 指标接入覆盖度
 * - /research/pig：猪智投看板（周期标签 / 信号 / 指标带 / 相位条 / 仓位建议 / EChart / 核心速览）
 * - /research/pig 行业知识库 Tab：机构图谱分组卡片 + 权威性原则 + 思维导图（P6）
 * - /research/pig 行情调研追踪 Tab：标的分析成分股对比表（registry 列渲染 + 行点击跳 /stock/:symbol）
 * - /research/pig 行情调研追踪 Tab：行业 ETF 表（P5 行情面，TuShare fund_daily 实拉数据）
 * - /market/industry/110000/110700：生猪养殖三级行业页的"进入投研工作台"banner 导航
 */

// 数据由 react-query 异步加载、图表异步渲染，统一放宽断言轮询窗口
const expect = baseExpect.configure({ timeout: 15_000 });

test("投研列表：生猪养殖行业卡片展示指标接入覆盖度", async ({ page }) => {
  await page.goto("/research");

  await expect(page.getByRole("heading", { name: "投研工作台" })).toBeVisible();

  // 行业卡片（由 registry 驱动，卡片整体可点击进入工作台）
  const card = page.locator(".ant-card").filter({ hasText: "生猪养殖" });
  await expect(card).toBeVisible();
  // 用 toContainText 规避嵌套元素严格模式冲突（卡片内文案可能命中多个后代节点）
  // 申万Ⅲ 分类标签 + 指标接入覆盖度 metricWithData/metricTotal（数值随数据变化，只断言形状）
  await expect(card).toContainText(/申万Ⅲ/);
  await expect(card).toContainText(/指标接入\s*\d+\s*\/\s*\d+/);
});

test("猪智投工作台：从行业卡片进入，看板核心区块完整渲染", async ({ page }) => {
  await page.goto("/research");

  // 通过点击行业卡片导航（而非直接 goto），覆盖列表 → 工作台的真实路径
  const card = page.locator(".ant-card").filter({ hasText: "生猪养殖" });
  await card.click();
  await page.waitForURL("**/research/pig");

  // ── 头部标签：周期阶段 + 当前信号（作用域限定在 ant-tag，避免命中信号面板容器）──
  const phaseTag = page.locator(".ant-tag").filter({ hasText: "周期阶段" });
  await expect(phaseTag).toBeVisible();
  await expect(phaseTag).toContainText(/繁荣|衰退|萧条|复苏/);
  const signalTag = page.locator(".ant-tag").filter({ hasText: "当前信号" });
  await expect(signalTag).toBeVisible();
  await expect(signalTag).toContainText(/买入|卖出|关注|空仓/);

  // ── 综合指标带：生猪均价卡片值非空（数值格式如 10.71 / 11,740） ──
  const strip = page.locator("section").filter({ hasText: "综合指标" });
  await expect(strip).toBeVisible();
  const avgName = strip.getByText("生猪均价", { exact: true });
  await expect(avgName).toBeVisible();
  const avgCard = avgName.locator("xpath=../.."); // 名称 span → 卡片根节点
  await expect(avgCard.getByText(/^[\d,]+(\.\d+)?$/)).toBeVisible();
  // 能繁母猪存栏在指标带中展示（quick_view 分组不含该指标，见报告说明）
  await expect(strip.getByText("能繁母猪存栏", { exact: true })).toBeVisible();

  // ── 周期相位条：四阶段齐全且恰有一个"当前"高亮 ───────────────
  const phaseCard = page.locator(".ant-card").filter({ hasText: "猪周期阶段定位" });
  await expect(phaseCard).toBeVisible();
  for (const label of ["繁荣", "衰退", "萧条", "复苏"]) {
    await expect(phaseCard.getByText(label, { exact: true })).toBeVisible();
  }
  await expect(phaseCard.getByText("当前", { exact: true })).toBeVisible();

  // ── 仓位管理建议：三段仓位（核心底仓/波段仓位/现金储备） ──────
  const posCard = page.locator(".ant-card").filter({ hasText: "仓位管理建议" });
  await expect(posCard).toBeVisible();
  for (const name of ["核心底仓", "波段仓位", "现金储备"]) {
    await expect(posCard.getByText(name, { exact: true })).toBeVisible();
  }

  // ── EChart 图表：价格 vs 成本 + 能繁母猪趋势（canvas 异步渲染） ──
  await expect(page.getByText("生猪价格 vs 行业成本")).toBeVisible();
  await expect(page.getByText("能繁母猪存栏趋势")).toBeVisible();
  await page.locator("canvas").first().waitFor({ state: "visible" });
  await expect
    .poll(async () => page.locator("canvas").count())
    .toBeGreaterThanOrEqual(2);

  // ── 核心指标速览：网格卡片含 quick 分组指标 ───────────────────
  const quickCard = page.locator(".ant-card").filter({ hasText: "核心指标速览" });
  await expect(quickCard).toBeVisible();
  await expect(quickCard.getByText("猪粮比", { exact: true })).toBeVisible();

  // ── 行业知识库 Tab：P6 占位文案 ────────────────────────────────
  // 放在最后：切换 Tab 后看板 pane 会被隐藏
  await page.getByRole("tab", { name: "行业知识库" }).click();
  // 分组 Card 标题锚定 .ant-card-head-title：SourceBadge 徽章文案（"官方基准"）
  // 也会出现在卡片正文里，须避免与其混淆
  for (const group of ["官方基准", "行业协会", "数据平台", "期货市场"]) {
    await expect(
      page.locator(".ant-card-head-title").filter({ hasText: group })
    ).toBeVisible();
  }
  // 机构条目（名称 + 权威性徽章）与数据权威性原则
  await expect(page.getByText("农业农村部", { exact: true })).toBeVisible();
  await expect(
    page.locator(".ant-card-head-title").filter({ hasText: "数据权威性使用原则" })
  ).toBeVisible();
  // 思维导图 EChart tree 渲染出 canvas（切换 Tab 后看板 canvas 已隐藏不计入）
  await expect(
    page.locator(".ant-card-head-title").filter({ hasText: "行业思维导图" })
  ).toBeVisible();
  await expect
    .poll(async () => page.locator("canvas:visible").count())
    .toBeGreaterThanOrEqual(1);
});

test("行情调研追踪：标的分析对比表渲染并跳转个股", async ({ page }) => {
  await page.goto("/research/pig");
  await page.getByRole("tab", { name: "行情调研追踪" }).click();

  // 成分股对比表：卡片 + 表头（固定行情列 + registry 下发的公司指标列）
  const card = page.locator(".ant-card").filter({ hasText: "标的分析 · 成分股对比" });
  await expect(card).toBeVisible();
  const table = card.locator(".ant-table");
  await expect(table).toBeVisible();
  // 限定 thead：开启横向滚动后 antd 会在 tbody 里加同文案的 measure 行（th）
  for (const header of ["代码", "名称", "总市值(亿)", "完全成本", "头均市值"]) {
    await expect(table.locator("thead th").filter({ hasText: header })).toBeVisible();
  }

  // 行数 ≥1（成分股来自 SW 分类）且行点击跳转 /stock/:symbol
  // （.ant-table-row 跳过 antd 横向滚动的隐藏 measure 行）
  await expect
    .poll(async () => table.locator("tbody tr.ant-table-row").count())
    .toBeGreaterThanOrEqual(1);
  await table.locator("tbody tr.ant-table-row").first().click();
  await page.waitForURL(/\/stock\/\d{6}$/);
});

test("行情调研追踪：行业 ETF 表展示畜牧 ETF 日线", async ({ page }) => {
  // 依赖后端 e2e 已触发 fetch-securities（TuShare 实拉一年日线）；行存在性用宽轮询兜底
  await page.goto("/research/pig");
  await page.getByRole("tab", { name: "行情调研追踪" }).click();

  const etfCard = page.locator(".ant-card").filter({ hasText: "行业 ETF" });
  await expect(etfCard).toBeVisible();
  for (const header of ["代码", "名称", "最新价", "涨跌幅", "成交量"]) {
    await expect(etfCard.locator("thead th").filter({ hasText: header })).toBeVisible();
  }
  // 拉取按钮存在（空态引导）；ETF 代码行渲染（registry 下发的 159865.SZ）
  await expect(etfCard.getByRole("button", { name: "拉取数据" })).toBeVisible();
  await expect
    .poll(async () => etfCard.locator("tbody tr.ant-table-row").count())
    .toBeGreaterThanOrEqual(1);
  await expect(etfCard.locator("tbody")).toContainText("159865.SZ");
});

test("生猪养殖三级行业页：投研工作台导航 banner", async ({ page }) => {
  // 申万 110000 农林牧渔 / 110700 养殖业（含 110702 生猪养殖 → 已产品化行业）
  await page.goto("/market/industry/110000/110700");

  const banner = page.locator(".ant-card").filter({ hasText: "进入投研工作台" });
  await expect(banner).toBeVisible();
  await expect(banner).toContainText(/已产品化/);

  await banner.getByRole("button", { name: "进入投研工作台" }).click();
  await page.waitForURL("**/research/pig");
  await expect(page.getByRole("heading", { name: "投研工作台" })).toBeVisible();
});
