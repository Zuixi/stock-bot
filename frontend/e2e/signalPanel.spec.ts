import { test, expect as baseExpect, type Page } from "@playwright/test";
import { join } from "node:path";
import { mkdirSync } from "node:fs";

/**
 * **投研工作台信号历史 diff 化**（Phase 4 §5.5）。
 *
 * ## 判据的重点是"变化"，不是"存在"
 *
 * `触发依据` 每条都重复出现时，页面**永远**能通过"存在性断言"（`toContainText` 找到了就算过）。
 * 本文件的判据是**从第 2 条起不再复述首条全文**——那才是 diff 化的全部意义：
 * 断言首条各段在"第 2 条起的区域"里**逐段不出现**（而不是整页不出现，整页当然要出现一次）。
 *
 * ## 为什么要 mock（而不是像 `responsive-b2.spec.ts` 那样真登录）
 *
 * `/industry/:key` 要 `research:read`，而本环境没有凭据（`E2E_RESEARCH_USER` 未设置）。
 * `research.spec.ts:24` 的教训是"**只 mock `/auth/session` 是假登录态**：没真 cookie 时
 * 接口 401 → 全局 `auth:unauthorized` 清态 → 布局反复重挂、卡片永不出现"。
 * 本文件不违反那条教训，而是走它的**完整版**：session 与页面消费的**全部**读面一起 mock
 * （dashboard 一个端点）——没有真接口参与，故不存在"部分真、部分假"造成的清态循环。
 *
 * ⚠️ 因此本文件**不能**替代真登录下的端到端验证；真凭据用例见
 * `responsive-b2.spec.ts` 的 `PriceCostChart`（那条是 skip-gated 的真登录）。
 * 这里的价值是：**diff 渲染逻辑**（分段、`老 → 新`、折叠）在浏览器里被逐条钉住，
 * 且不需要任何凭据就能天天跑。
 *
 * ## 数据来源
 *
 * `signal_history` 的 `reason` 逐字取自库内真实行（`industry_signals` where
 * `industry_key='pig'`，2026-10-13 / 10-12 / 10-09）；`positions` 与 `basis` 同样取实测值。
 * 只有"连续无变化"那两条是**刻意构造**的（真实序列里每天都有一点变化，
 * 造不出来 —— 那是测试目的，不是数据造假：折叠分支必须有输入才能被验证）。
 */

const expect = baseExpect.configure({ timeout: 20_000 });

const EVIDENCE_DIR = join(
  process.cwd(),
  "..",
  ".superpowers",
  "sdd",
  "2026-10-12-investment-research-platform-ia",
  "evidence"
);
mkdirSync(EVIDENCE_DIR, { recursive: true });

const USER = {
  id: "usr-e2e-sig",
  username: "sig_reader",
  email: "sig@example.com",
  display_name: "信号读者",
  status: "active",
  is_superuser: false,
  roles: ["reader"],
  permissions: ["stocks:read", "research:read"],
  created_at: "2026-10-01T00:00:00Z",
};

/** 库内真实 reason（逐字，未改标点；分隔符是全角分号） */
const R_1013 =
  "生猪均价 10.07 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.25 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";
const R_1012 =
  "生猪均价 10.15 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.34 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";
const R_1009 =
  "生猪均价 10.17 元/kg 低于行业平均成本 13.45 元/kg，全行业亏损；猪粮比 4.36 处于一级预警区间，行业处于亏损预警区间；能繁存栏环比连续 3 个月回落，产能去化进行中；产能去化提速，左侧布局窗口临近；猪粮比触发一级预警，国储收储预期强化底部判断";

const POSITIONS = [
  { name: "核心底仓", role: "做周期 · 长持", desc: "成本与现金流领先的头部企业，穿越周期持有", pct: 50, color: "#1677ff" },
  { name: "波段仓位", role: "做波动 · 择时", desc: "畜牧 ETF / 高 β 龙头，跟随信号加减", pct: 30, color: "#faad14" },
  { name: "现金储备", role: "底部加仓弹药", desc: "货基 / 国债逆回购，预留极端行情", pct: 20, color: "#94a3b8" },
];

function signalRow(signalType: string, effectiveDate: string, reason: string) {
  return { signal_type: signalType, phase: "depression", effective_date: effectiveDate, reason, positions: POSITIONS };
}

const PHASES = [
  { key: "prosperity", label: "繁荣", desc: "价格高位 · 产能扩张", active: false },
  { key: "recession", label: "衰退", desc: "盈利收窄 · 库存累积", active: false },
  { key: "depression", label: "萧条", desc: "深度亏损 · 产能出清", active: true },
  { key: "recovery", label: "复苏", desc: "价格回升 · 补栏启动", active: false },
];

function metricRow(key: string, name: string, value: number | null, unit: string | null) {
  return {
    metric_key: key,
    name,
    value,
    unit,
    tier: "core",
    source: "e2e",
    freq: "daily",
    period: "2026-10-13",
    as_of: "2026-10-13",
    as_of_source: "period",
    published_at: null,
    caliber_version: null,
    caliber_version_registered: null,
    scope_key: null,
    delta: null,
    warn: null,
    warn_severity: null,
    spark: null,
    description: "",
    missing_reason: null,
    coverage: null,
  };
}

/** 近 6 个月的月度价格/成本（供 `PriceCostChart` 与 §5.6 的剪刀差小图共用） */
const MONTHS = ["2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10"];
const PRICES = [13.2, 12.4, 11.8, 11.1, 10.4, 10.07];
const COSTS = [13.1, 13.2, 13.3, 13.4, 13.45, 13.45];

/**
 * `signal_history` 的构造（顺序 = 后端口径：**最新在前**）：
 *   [0] 10-13  首条 → 完整触发依据
 *   [1] 10-12  与 [0] 比：2 处变化（猪粮比、生猪均价）
 *   [2] 10-09  与 [1] 比：1 处变化（猪粮比）
 *   [3] 10-08  与 [2] 比：0 处变化 ┐ 连续无变化 → 折叠成一行
 *   [4] 10-07  与 [3] 比：0 处变化 ┘
 */
const DASHBOARD = {
  industry: {
    key: "pig",
    name: "生猪养殖",
    description: "e2e mock",
    sw_l3_codes: ["110701"],
  },
  as_of: "2026-10-13",
  data_source: "e2e",
  strip: [
    metricRow("hog_price", "生猪均价", 10.07, "元/kg"),
    metricRow("hog_corn_ratio", "猪粮比", 4.25, null),
    metricRow("sow_inventory", "能繁母猪存栏", 4038, "万头"),
  ],
  quick_view: [metricRow("industry_cost_avg", "行业平均成本", 13.45, "元/kg")],
  trends: {
    price_vs_cost: {
      periods: MONTHS,
      series: { 生猪均价: PRICES, 行业平均成本: COSTS },
      reference: null,
    },
  },
  cycle: {
    phase: "depression",
    phase_index: 2,
    phases: PHASES,
    reasons: ["价格低于行业平均成本", "猪粮比处于一级预警区间"],
    basis: { cost: 13.45, loss: true, price: 10.07, ratio: 4.2489, ratio_band: "一级预警", sow_consecutive_decline: 3 },
  },
  signal: signalRow("关注", "2026-10-13", R_1013),
  signal_history: [
    signalRow("关注", "2026-10-13", R_1013),
    signalRow("关注", "2026-10-12", R_1012),
    signalRow("关注", "2026-10-09", R_1009),
    signalRow("关注", "2026-10-08", R_1009),
    signalRow("关注", "2026-10-07", R_1009),
  ],
};

async function mockWorkbench(page: Page) {
  await page.route("**/auth/session", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(USER) })
  );
  await page.route("**/api/v1/industries/*/dashboard*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(DASHBOARD) })
  );
}

/** 打开工作台并定位信号面板所在卡片（`.ant-card` + 标题文本） */
async function openSignalPanel(page: Page) {
  await mockWorkbench(page);
  await page.goto("/industry/pig", { waitUntil: "domcontentloaded" });
  const card = page.locator(".ant-card").filter({ hasText: "交易信号面板" });
  await expect(card).toHaveCount(1);
  await expect(card.locator(".ant-timeline")).toBeVisible();
  return card;
}

test("第 2 条起不复述首条的触发依据（首条给全文，其余只给变化项）", async ({ page }) => {
  const card = await openSignalPanel(page);

  // 首条：完整触发依据（**全文**，逐段都在）
  const full = card.getByTestId("signal-history-full");
  await expect(full).toHaveCount(1);
  const firstText = await full.innerText();
  expect(firstText, "首条必须是完整触发依据").toBe(R_1013);

  // 第 2 条起：**首条那 5 段里未变化的 3 段，一律不得出现在第 2 条起的区域里**
  // （作用域限定到时间线的非首行——整页当然要有一次首条全文，那是首行）
  const allRowTexts = await card.locator("[data-testid^='signal-history-row-']").allInnerTexts();
  expect(allRowTexts.length, "历史行数应为 4（3 普通 + 1 折叠）").toBe(4);
  const laterText = allRowTexts.slice(1).join("\n");

  for (const unchanged of [
    "能繁存栏环比连续 3 个月回落，产能去化进行中",
    "产能去化提速，左侧布局窗口临近",
    "猪粮比触发一级预警，国储收储预期强化底部判断",
  ]) {
    expect(laterText, `未变化的段被复述了：${unchanged}`).not.toContain(unchanged);
  }

  // 首条全文**整串**也不得在后续行里再次出现（最强的"没有复述"判据）
  expect(laterText, "首条触发依据全文在后续行里被复述").not.toContain(R_1013);
});

test("变化项以「老 → 新」渲染（猪粮比 4.25 → 4.34、生猪均价 10.07 → 10.15）", async ({ page }) => {
  const card = await openSignalPanel(page);

  const changes = card.getByTestId("signal-history-changes-1");
  await expect(changes).toHaveCount(1);
  const text = await changes.innerText();

  expect(text, "缺猪粮比的变化").toContain("猪粮比 4.34");
  expect(text, "缺猪粮比的旧值").toContain("猪粮比 4.25");
  expect(text, "缺生猪均价的变化").toContain("生猪均价 10.15");
  expect(text, "缺生猪均价的旧值").toContain("生猪均价 10.07");
  // 箭头是"值变了"的专用标记：两处都应是 value 型
  expect(await changes.locator("[data-change-kind='value']").count()).toBe(2);
  expect(await changes.locator("[data-change-kind='changed']").count()).toBe(0);

  // 第 3 条（10-09 vs 10-12）：也是 2 处变化（生猪均价 10.15→10.17、猪粮比 4.34→4.36）
  const changes2 = card.getByTestId("signal-history-changes-2");
  await expect(changes2).toHaveCount(1);
  expect(await changes2.locator("[data-change-kind='value']").count()).toBe(2);
  const text2 = await changes2.innerText();
  expect(text2).toContain("猪粮比 4.36");
  expect(text2).toContain("猪粮比 4.34");
  expect(text2).toContain("生猪均价 10.17");
  expect(text2).toContain("生猪均价 10.15");
});

test("连续无变化折叠为「连续 N 条无变化」，且信号条数不丢", async ({ page }) => {
  const card = await openSignalPanel(page);

  const collapsed = card.locator("[data-testid^='signal-history-collapsed-']");
  await expect(collapsed).toHaveCount(1);
  await expect(collapsed).toContainText("连续 2 条无变化");
  expect(await collapsed.getAttribute("data-collapsed-count"), "折叠条数必须可机械核对").toBe("2");

  // 条数守恒：5 条历史 = 3 条普通行（0/1/2）+ 1 条折叠行（代表 2 条）
  const rows = card.locator("[data-testid^='signal-history-row-']");
  await expect(rows).toHaveCount(4);
  const collapsedCount = Number(await collapsed.getAttribute("data-collapsed-count"));
  expect(3 + collapsedCount, "折叠不得吞掉信号条目").toBe(5);
});

test("视觉存档：信号面板（diff 化后的历史）", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const card = await openSignalPanel(page);
  await card.screenshot({
    path: join(EVIDENCE_DIR, "phase4-signal-panel-diff.png"),
  });
});

test("§5.6：左栏补上的「价格 vs 成本剪刀差」在同一份 payload 上渲染", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockWorkbench(page);
  await page.goto("/industry/pig", { waitUntil: "domcontentloaded" });

  const scissors = page.getByTestId("cost-scissors");
  await expect(scissors).toHaveCount(1);
  // 6 个月的点（与 mock 的 MONTHS 等长）
  expect(await scissors.getAttribute("data-months"), "剪刀差点数应等于 payload 的有效月数").toBe("6");
  await expect(scissors.getByTestId("cost-scissors-latest")).toContainText("元/kg");
  // 点数为 1 时整块不渲染（避免画一条没有意义的线）
  const svg = scissors.locator("svg");
  await expect(svg).toHaveCount(1);

  await page.screenshot({
    path: join(EVIDENCE_DIR, "phase4-workbench-left-column.png"),
    fullPage: true,
  });
});
