import { test, expect as baseExpect } from "@playwright/test";

/**
 * 投研工作台浏览器级 E2E（依赖运行中的 docker 栈，数据为实盘/混合源，故断言只锚定结构与中文标签，不锚定具体数值）。
 * - /research：行业卡片（生猪养殖）+ 指标接入覆盖度
 * - /research/pig：猪智投看板（周期标签 / 信号 / 指标带 / 相位条 / 仓位建议 / EChart / 核心速览）
 * - /research/pig 行业知识库 Tab：机构图谱分组卡片 + 权威性原则 + 思维导图（P6）
 * - /research/pig 行情调研追踪 Tab：标的分析成分股对比表（registry 列渲染 + 行点击跳 /stock/:symbol）
 * - /research/pig 行情调研追踪 Tab：行业 ETF 表（P5 行情面，TuShare fund_daily 实拉数据）
 * - /market/industry/110000/110700：生猪养殖三级行业页的"进入投研工作台"banner 导航
 * - 泛化验证（P6）：白羽肉鸡（broiler）第二行业卡片 → 同一工作台零新页面渲染
 * - /research：行业卡片等高（描述单行省略号 + Col flex 拉伸兜底）
 * - /research/pig：面包屑"投研"可点击返回行业列表
 */

// 数据由 react-query 异步加载、图表异步渲染，统一放宽断言轮询窗口
const expect = baseExpect.configure({ timeout: 15_000 });

const dashboardFixture = {
  industry: {
    key: "pig",
    name: "生猪养殖",
    description: "生猪养殖行业周期投研",
    sw_l3_codes: ["110702"],
  },
  as_of: "2026-09-03",
  data_source: "akshare",
  strip: [
    {
      metric_key: "hog_price",
      name: "生猪均价",
      value: 14.8,
      unit: "元/kg",
      tier: "highfreq",
      source: "akshare_soozhu",
      freq: "daily",
      period: "2026-09-03",
      delta: { pct: 2.1, direction: "up", label: "环比" },
      warn: null,
      warn_severity: null,
      spark: [14.2, 14.5, 14.8],
      description: "全国生猪均价",
    },
  ],
  quick_view: [
    {
      metric_key: "hog_corn_ratio",
      name: "猪粮比",
      value: 6.2,
      unit: null,
      tier: "calc",
      source: "derived",
      freq: "daily",
      period: "2026-09-03",
      delta: null,
      warn: null,
      warn_severity: null,
      spark: null,
      description: "猪价与玉米价格比值",
    },
  ],
  trends: {
    price_vs_cost: {
      periods: ["2026-07-31", "2026-08-31"],
      series: { 生猪均价: [14.2, 14.8], 行业平均完全成本: [15.5, 15.4] },
      reference: null,
    },
    sow_inventory: {
      periods: ["2026-07-31", "2026-08-31"],
      series: { 能繁母猪存栏: [3920, 3904] },
      reference: { label: "正常保有量", value: 3900, note: null, effective_from: "2025-01-01" },
    },
  },
  cycle: {
    phase: "recovery",
    phase_index: 3,
    phases: [
      { key: "prosperity", label: "繁荣", desc: "盈利高位", active: false },
      { key: "recession", label: "衰退", desc: "盈利收窄", active: false },
      { key: "depression", label: "萧条", desc: "产能去化", active: false },
      { key: "recovery", label: "复苏", desc: "价格修复", active: true },
    ],
    reasons: ["猪价修复"],
    basis: {},
  },
  signal: {
    signal_type: "买入",
    phase: "recovery",
    effective_date: "2026-08-01",
    reason: "周期进入复苏",
    positions: [
      { name: "核心底仓", role: "core", desc: "长期配置", pct: 50, color: "#ef4444" },
      { name: "波段仓位", role: "tactical", desc: "趋势增强", pct: 30, color: "#faad14" },
      { name: "现金储备", role: "cash", desc: "风险缓冲", pct: 20, color: "#8c8c8c" },
    ],
  },
  signal_is_stale: false,
  data_quality: {
    as_of: "2026-09-03",
    status: "healthy",
    signal_ready: true,
    ready_count: 3,
    missing_count: 0,
    stale_count: 0,
    rejected_count: 0,
    partial_count: 0,
    details: [
      {
        metric_key: "hog_price",
        status: "ready",
        source: "akshare_soozhu",
        freq: "daily",
        period: "2026-09-03",
        age_days: 0,
        reason: null,
        entity_coverage: null,
      },
    ],
  },
  signal_events: [
    {
      event_date: "2026-08-01",
      event_sequence: 1,
      signal_type: "买入",
      phase: "recovery",
      previous_signal_type: "关注",
      previous_phase: "depression",
      rule_version: "pig-cycle-v1",
      verification_supported: true,
      evaluations: [
        {
          horizon_days: 30,
          status: "confirmed",
          target_date: "2026-08-31",
          score: 80,
          criteria_results: [{ metric_key: "hog_price", status: "met", score: "30" }],
          insufficient_reasons: [],
          evaluated_at: "2026-09-01T08:00:00Z",
        },
        {
          horizon_days: 90,
          status: "pending",
          target_date: "2026-10-30",
          score: null,
          criteria_results: [],
          insufficient_reasons: [],
          evaluated_at: null,
        },
      ],
    },
  ],
  verification_summary: {
    completed_directional_evaluations: 1,
    confirmed: 1,
    partially_confirmed: 0,
    invalidated: 0,
    inconclusive: 0,
    pending: 1,
    accuracy_pct: null,
  },
  signal_history: [],
};

async function mockDashboard(page: import("@playwright/test").Page, overrides: Record<string, unknown>) {
  await page.route("**/api/v1/industries/pig/dashboard", async (route) => {
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({ ...dashboardFixture, ...overrides }),
    });
  });
}

test("data quality: unavailable stale signal shows warning and retained-signal disclosure", async ({ page }) => {
  await mockDashboard(page, {
    signal_is_stale: true,
    data_quality: {
      ...dashboardFixture.data_quality,
      status: "unavailable",
      signal_ready: false,
      ready_count: 1,
      missing_count: 1,
      stale_count: 1,
      details: [
        ...dashboardFixture.data_quality.details,
        {
          metric_key: "hog_corn_ratio",
          status: "stale",
          source: "derived",
          freq: "daily",
          period: "2026-08-20",
          age_days: 14,
          reason: "超过 7 天新鲜度要求",
          entity_coverage: null,
        },
        {
          metric_key: "sow_inventory_mom",
          status: "missing",
          source: null,
          freq: "monthly",
          period: null,
          age_days: null,
          reason: "缺少必需指标",
          entity_coverage: null,
        },
      ],
    },
  });

  await page.goto("/research/pig");

  const quality = page.getByTestId("industry-data-quality");
  await expect(quality).toBeVisible();
  await expect(quality).toContainText("数据质量异常");
  await expect(quality).toContainText("当前展示为最近一次有效信号，本次因数据不足未更新");
  await expect(page.getByText("最近一次有效信号", { exact: true })).toBeVisible();
});

test("signal verification: event timeline renders confirmed 30d score and pending 90d target", async ({ page }) => {
  await mockDashboard(page, {});

  await page.goto("/research/pig");

  const timeline = page.getByTestId("signal-event-timeline");
  await expect(timeline).toBeVisible();
  await expect(page.getByTestId("signal-evaluation-30")).toContainText("30天 已确认");
  await expect(page.getByTestId("signal-evaluation-30")).toContainText("80分");
  await expect(page.getByTestId("signal-evaluation-90")).toContainText("目标日期 2026-10-30");

  const helpTrigger = page.getByRole("button", { name: "查看信号说明" });
  await helpTrigger.focus();
  await expect(helpTrigger).toBeFocused();
  await expect(page.getByText("右侧趋势确认，做多", { exact: true })).toBeVisible();
});

test("signal verification: same-day events keep unique containers, keys, and primary evaluation IDs", async ({ page }) => {
  const olderEvent = {
    ...dashboardFixture.signal_events[0],
    event_sequence: 1,
    signal_type: "关注",
    previous_signal_type: "空仓",
    evaluations: dashboardFixture.signal_events[0].evaluations.map((evaluation) => ({
      ...evaluation,
      score: evaluation.horizon_days === 30 ? 60 : evaluation.score,
    })),
  };
  const latestEvent = {
    ...dashboardFixture.signal_events[0],
    event_sequence: 2,
    previous_signal_type: "关注",
  };
  await mockDashboard(page, { signal_events: [latestEvent, olderEvent] });

  await page.goto("/research/pig");

  const latestContainer = page.getByTestId("signal-event-2026-08-01-2");
  const olderContainer = page.getByTestId("signal-event-2026-08-01-1");
  await expect(latestContainer).toBeVisible();
  await expect(olderContainer).toBeVisible();
  await expect(latestContainer.locator('[data-evaluation-id="2026-08-01-2-30"]')).toContainText("80分");
  await expect(olderContainer.locator('[data-evaluation-id="2026-08-01-1-30"]')).toContainText("60分");
  await expect(page.getByTestId("signal-evaluation-30")).toHaveCount(1);
  await expect(page.getByTestId("signal-evaluation-90")).toHaveCount(1);
});

test("signal verification: null current signal preserves summary and historical events", async ({ page }) => {
  await mockDashboard(page, { signal: null, cycle: null });

  await page.goto("/research/pig");

  const signalCard = page.locator(".ant-card").filter({ hasText: "交易信号面板" });
  await expect(signalCard.getByText("暂无有效信号", { exact: true })).toBeVisible();
  await expect(signalCard).toContainText("已验证 1");
  await expect(signalCard).toContainText("确认 1");
  await expect(page.getByTestId("signal-event-2026-08-01-1")).toBeVisible();
  await expect(page.getByTestId("signal-evaluation-30")).toContainText("30天 已确认");
  await expect(page.locator(".ant-tag").filter({ hasText: "信号状态" })).toContainText("待评估");
  await expect(page.getByText("当前信号 待评估", { exact: true })).toHaveCount(0);
});

test("signal verification: inconclusive reason and healthy compact status render", async ({ page }) => {
  await mockDashboard(page, {
    signal_events: [
      {
        ...dashboardFixture.signal_events[0],
        evaluations: [
          {
            ...dashboardFixture.signal_events[0].evaluations[0],
            status: "inconclusive",
            score: null,
            insufficient_reasons: ["能繁母猪存栏环比缺少目标期观测"],
          },
          dashboardFixture.signal_events[0].evaluations[1],
        ],
      },
    ],
    verification_summary: {
      ...dashboardFixture.verification_summary,
      completed_directional_evaluations: 0,
      confirmed: 0,
      inconclusive: 1,
    },
  });

  await page.goto("/research/pig");

  await expect(page.getByTestId("industry-data-quality")).toContainText("数据质量正常");
  await expect(page.getByTestId("industry-data-quality").locator(".ant-alert")).toHaveCount(0);
  await expect(page.getByTestId("signal-evaluation-30")).toContainText("证据不足");
  await expect(page.getByTestId("signal-evaluation-30")).toContainText("能繁母猪存栏环比缺少目标期观测");
});

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

  // 先等 Tab 栏挂载：/research 列表卡片也带 周期阶段/当前信号 Tag（P6），
  // SPA 路由切换瞬间旧列表仍在 DOM，直接断言会撞严格模式多元素
  await expect(page.getByRole("tab", { name: "投资看板" })).toBeVisible();

  // ── 头部标签：质量门禁未通过时必须明确降级为待评估 ──────────────
  const phaseTag = page.locator(".ant-tag").filter({ hasText: "周期阶段" });
  await expect(phaseTag).toBeVisible();
  await expect(phaseTag).toContainText(/繁荣|衰退|萧条|复苏|待评估/);
  const signalTag = page.locator(".ant-tag").filter({ hasText: /当前信号|信号状态/ });
  await expect(signalTag).toBeVisible();
  await expect(signalTag).toContainText(/买入|卖出|关注|空仓|待评估/);

  // ── 综合指标带：生猪均价卡片值非空（数值格式如 10.71 / 11,740） ──
  const strip = page.locator("section").filter({ hasText: "综合指标" });
  await expect(strip).toBeVisible();
  const avgName = strip.getByText("生猪均价", { exact: true });
  await expect(avgName).toBeVisible();
  const avgCard = avgName.locator("xpath=../.."); // 名称 span → 卡片根节点
  await expect(avgCard.getByText(/^[\d,]+(\.\d+)?$/)).toBeVisible();
  // 能繁母猪存栏在指标带中展示（quick_view 分组不含该指标，见报告说明）
  await expect(strip.getByText("能繁母猪存栏", { exact: true })).toBeVisible();

  // ── 周期/仓位区：有信号时渲染业务值，无信号时展示明确空态 ──────
  const phaseCard = page.locator(".ant-card").filter({ hasText: "猪周期阶段定位" });
  await expect(phaseCard).toBeVisible();
  const hasActiveCycle = (await phaseCard.getByText("当前", { exact: true }).count()) > 0;
  if (hasActiveCycle) {
    for (const label of ["繁荣", "衰退", "萧条", "复苏"]) {
      await expect(phaseCard.getByText(label, { exact: true })).toBeVisible();
    }
  } else {
    await expect(phaseCard.getByText("暂无有效周期阶段", { exact: true })).toBeVisible();
  }

  const posCard = page.locator(".ant-card").filter({ hasText: "仓位管理建议" });
  await expect(posCard).toBeVisible();
  if (hasActiveCycle) {
    for (const name of ["核心底仓", "波段仓位", "现金储备"]) {
      await expect(posCard.getByText(name, { exact: true })).toBeVisible();
    }
  } else {
    await expect(posCard.getByText("暂无仓位建议", { exact: true })).toBeVisible();
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

test("投研列表：行业卡片状态行遵循质量门禁", async ({ page, request }) => {
  await page.goto("/research");

  const pigCard = page.locator(".ant-card").filter({ hasText: "生猪养殖" });
  await expect(pigCard).toBeVisible();
  const dashboard = await (await request.get("http://localhost:8000/api/v1/industries/pig/dashboard")).json();
  if (dashboard.data_quality.signal_ready) {
    await expect(pigCard.locator(".ant-tag").filter({ hasText: "周期阶段" })).toHaveText(
      /周期阶段 · (繁荣|衰退|萧条|复苏)/
    );
    await expect(pigCard.locator(".ant-tag").filter({ hasText: "当前信号" })).toHaveText(
      /当前信号 (买入|卖出|关注|空仓)/
    );
  } else {
    await expect(pigCard.locator(".ant-tag").filter({ hasText: /周期阶段|当前信号/ })).toHaveCount(0);
  }
});

test("泛化验证：白羽肉鸡第二行业卡片零新页面进入工作台", async ({ page }) => {  // P6 泛化验证：registry 配置驱动，前端零改动 —— 列表同时出现两个行业卡片
  await page.goto("/research");

  const pigCard = page.locator(".ant-card").filter({ hasText: "生猪养殖" });
  const broilerCard = page.locator(".ant-card").filter({ hasText: "白羽肉鸡" });
  await expect(pigCard).toBeVisible();
  await expect(broilerCard).toBeVisible();

  // broiler mock ingest 具备有效信号；pig 是否展示状态由质量门禁决定
  await expect(broilerCard).toContainText(/周期阶段/);
  await expect(broilerCard).toContainText(/当前信号/);

  // 点击 broiler 卡片 → 复用同一工作台路由与组件，指标带渲染 registry 下发的肉鸡指标
  await broilerCard.click();
  await page.waitForURL("**/research/broiler");

  const strip = page.locator("section").filter({ hasText: "综合指标" });
  await expect(strip).toBeVisible();
  await expect(strip.getByText("鸡苗价格", { exact: true })).toBeVisible();
  await expect(strip.getByText("毛鸡价格", { exact: true })).toBeVisible();
  const chickName = strip.getByText("鸡苗价格", { exact: true });
  const chickCard = chickName.locator("xpath=../..");
  await expect(chickCard.getByText(/^[\d,]+(\.\d+)?$/)).toBeVisible();

  // 周期相位条同样由 broiler registry 配置驱动渲染
  const phaseCard = page.locator(".ant-card").filter({ hasText: "周期阶段定位" });
  await expect(phaseCard).toBeVisible();
  await expect(phaseCard.getByText("当前", { exact: true })).toBeVisible();
});

test("投研列表：行业卡片任意断点下等高", async ({ page }) => {
  // 描述单行省略号消除换行撑高 + Col display:flex / Card height:100% 兜底标签换行等场景
  await page.goto("/research");

  const pigCard = page.locator(".ant-card").filter({ hasText: "生猪养殖" });
  const broilerCard = page.locator(".ant-card").filter({ hasText: "白羽肉鸡" });
  await expect(pigCard).toBeVisible();
  await expect(broilerCard).toBeVisible();

  const pigHeight = (await pigCard.boundingBox())?.height ?? 0;
  const broilerHeight = (await broilerCard.boundingBox())?.height ?? 0;
  // 子像素渲染允许 ≤1px 容差
  expect(Math.abs(pigHeight - broilerHeight)).toBeLessThanOrEqual(1);
});

test("工作台面包屑：投研项可点击返回行业列表", async ({ page }) => {
  await page.goto("/research/pig");

  // 面包屑首项为 react-router Link，末项"工作台"为当前页（无链接）
  const crumbLink = page.getByRole("link", { name: "投研" });
  await expect(crumbLink).toBeVisible();
  await crumbLink.click();

  await page.waitForURL("**/research");
  await expect(page.locator(".ant-card").filter({ hasText: "生猪养殖" })).toBeVisible();
});
