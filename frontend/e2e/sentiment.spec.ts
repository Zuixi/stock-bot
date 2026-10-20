import { expect, test, type Page } from "@playwright/test";

/**
 * `/sentiment` 情绪分析真页面 E2E（S1.8–S1.13）。
 *
 * 覆盖批准稿里**会被静默做错**的四件事：
 *  1. hero 的合成值 + 「本日由 N/11 个成分指标合成 · 生效权重 w」必须上屏（Ruling 66：最新值的"薄"要可见）；
 *  2. `insufficient_history` ⇒ 显示「样本不足」，**不给分位数字、也不画标记**（绝不 0、绝不假位置）；
 *  3. 两个未落地模块（S1.14 散户舆情 / S2.2 情绪预警）就地给结论，而不是空白或假图；
 *  4. 6–8 条口径注真的在页面上（按 metric 就近放置），且分位只出现在后端给了分位的行上。
 *
 * 页面消费的三个读面都是受保护的，故统一 mock `/auth/session` 为已登录；
 * 匿名用例另测（`/sentiment` 现在 `requiresAuth`）。
 */

const USER = {
  id: "usr-e2e-sent",
  username: "sent_reader",
  email: "sent@example.com",
  display_name: "情绪读者",
  status: "active",
  is_superuser: false,
  roles: ["reader"],
  // `research:read` **必需**：IA §1 把 `/sentiment` 与 `/industry` 定为同一权限档
  // （页面消费 `/api/v1/metrics/*`，该读面要求 `research:read`；匿名 401、普通交易员 403）。
  // 这里原先只有 `stocks:read`，是权限收口之前的写法 ⇒ 页面渲染 403 视图、hero 不存在，
  // 导致本文件 11 条用例失败（症状看着像"页面坏了"，实为 mock 用户权限不足）。
  permissions: ["stocks:read", "research:read"],
  created_at: "2026-10-01T00:00:00Z",
};

/** `GET /api/v1/market/sentiment/composite`（契约冻结稿的字节级复刻）。 */
const COMPOSITE = {
  as_of: "2026-10-12",
  value: 32.8157,
  window: 1260,
  sample_count: 1260,
  percentile: 5.2,
  insufficient_history: false,
  legs: ["if_basis", "ic_basis", "im_basis"],
  leg_count: 3,
  total_legs: 11,
  effective_weight: 0.23,
  min_effective_weight: 0.2,
  caliber_version: "s1.13-v1",
  // 新鲜度三态（2026-10-16 新增）：追平 ⇒ 不显示任何滞后提示
  expected_as_of: "2026-10-12",
  lag_trading_days: 0,
  stale: false,
  // 临时值三态：false = 终值 ⇒ 不显示任何"待重算"提示
  provisional: false,
};

/** 临时值（缺融资融券 T+1 披露）：必须明写"待上游披露后重算、可能变化"，且**不**承诺时点。 */
const COMPOSITE_PROVISIONAL = {
  ...COMPOSITE,
  provisional: true,
};

/** 判不出临时值（缺参与集登记或缺交易日历）：**不得**沉默，更不得塌成"终值"。 */
const COMPOSITE_PROVISIONAL_UNKNOWN = {
  ...COMPOSITE,
  provisional: null,
};

/** 落后的读数（后端退回上一个「各成分指标齐备」的交易日）—— 必须明写滞后几个交易日。 */
const COMPOSITE_STALE = {
  ...COMPOSITE,
  as_of: "2026-10-09",
  value: 44.5068,
  legs: ["if_basis", "ic_basis", "im_basis", "qvix_50etf"],
  leg_count: 10,
  effective_weight: 0.88,
  expected_as_of: "2026-10-14",
  lag_trading_days: 3,
  stale: true,
};

/**
 * 当日被发布闸门拦下（2026-10-19 生产同形）：8/11 条腿、生效权重 0.72 < 0.80。
 * 缺：两融 ×2（T+1 披露）+ QVIX（源侧滞后）。
 */
const WITHHELD_1019 = {
  as_of: "2026-10-19",
  leg_count: 8,
  total_legs: 11,
  effective_weight: 0.72,
  min_effective_weight: 0.8,
  missing: [
    { key: "qvix_50etf", weight: 0.12, late_arrival: false, reason: "source_not_updated" },
    { key: "margin_financing_balance", weight: 0.08, late_arrival: true, reason: "source_empty" },
    { key: "margin_financing_buy_ratio", weight: 0.08, late_arrival: true, reason: "source_empty" },
  ],
  missing_reason: "below_publish_weight",
};

/** 落后**且有被拦当日**：页面必须说清"当日 8/11、0.72、未达阈值"，而不是拿期望日冒充齐备日。 */
const COMPOSITE_STALE_WITH_HELD = {
  ...COMPOSITE_STALE,
  // 与生产 2026-10-19 23:00 同形：上屏 10-16，日历期望日 10-19，滞后 1 个交易日
  as_of: "2026-10-16",
  value: 32.7876,
  expected_as_of: "2026-10-19",
  lag_trading_days: 1,
  latest_withheld: WITHHELD_1019,
};

/** 判不出新鲜度：三字段全 null —— 页面**不得**沉默（沉默会被读成"新鲜"）。 */
const COMPOSITE_FRESHNESS_UNKNOWN = {
  ...COMPOSITE,
  expected_as_of: null,
  lag_trading_days: null,
  stale: null,
};

/** 只有分位、没有分位数字的退化响应（样本不足）。 */
const COMPOSITE_INSUFFICIENT = {
  ...COMPOSITE,
  percentile: null,
  insufficient_history: true,
  sample_count: 42,
};

interface MetricSeed {
  key: string;
  name: string;
  value: number | null;
  unit: string | null;
  /** 覆盖默认 freq（季度/月频/周度的指标） */
  freq?: string;
  extra?: Record<string, unknown>;
}

/** 模块表用到的 37 个 metric_key 的真实形状（值取可读的样例数）。 */
const METRIC_SEEDS: MetricSeed[] = [
  { key: "market_advance_count", name: "上涨家数", value: 3210, unit: "家" },
  { key: "market_decline_count", name: "下跌家数", value: 1500, unit: "家" },
  { key: "advance_decline_ratio", name: "涨跌比", value: 2.14, unit: "" },
  { key: "limit_up_down_ratio", name: "涨跌停比", value: 37.5, unit: "" },
  { key: "above_ma20_ratio", name: "站上 20 日均线占比", value: 48.6, unit: "%" },
  { key: "limit_up_break_ratio", name: "炸板率", value: 34.21, unit: "%" },
  { key: "max_consecutive_limit_up", name: "连板高度", value: 4, unit: "板" },
  { key: "first_board_promotion_ratio", name: "首板晋级率", value: 15.85, unit: "%" },
  { key: "market_turnover_amount", name: "全市场成交额", value: 15832.4, unit: "亿元" },
  { key: "idx_turnover_ratio_sse", name: "上证指数换手率", value: 1.02, unit: "%" },
  { key: "idx_turnover_ratio_szse", name: "深证成指换手率", value: 1.44, unit: "%" },
  { key: "idx_turnover_ratio_bse50", name: "北证50换手率", value: 0.88, unit: "%" },
  { key: "idx_turnover_ratio_star", name: "科创综指换手率", value: 1.31, unit: "%" },
  { key: "idx_turnover_ratio_chinext", name: "创业板指换手率", value: 1.77, unit: "%" },
  { key: "idx_turnover_ratio_sse50", name: "上证50换手率", value: 0.41, unit: "%" },
  { key: "idx_turnover_ratio_csi300", name: "沪深300换手率", value: 0.92, unit: "%" },
  { key: "margin_financing_balance", name: "融资余额", value: 24315.7, unit: "亿元" },
  { key: "margin_financing_buy_ratio", name: "融资买入额占比", value: 9.83, unit: "%" },
  { key: "margin_short_balance", name: "融券余额", value: 512.3, unit: "亿元" },
  // QVIX 是第三方源、有发布滞后：当日落 NULL + 原因码（不落 0、不落最近值）
  {
    key: "qvix_50etf",
    name: "50ETF期权隐含波动率",
    value: null,
    unit: null,
    extra: { missing_reason: "source_not_updated" },
  },
  { key: "qvix_300etf", name: "300ETF期权隐含波动率", value: 19.4, unit: null },
  { key: "qvix_500etf", name: "500ETF期权隐含波动率", value: 24.1, unit: null },
  { key: "qvix_cyb_etf", name: "创业板ETF期权隐含波动率", value: 28.7, unit: null },
  { key: "qvix_kcb_etf", name: "科创50ETF期权隐含波动率", value: 31.2, unit: null },
  { key: "if_basis", name: "IF股指期货基差", value: -62.18, unit: "点" },
  { key: "ic_basis", name: "IC股指期货基差", value: -120.42, unit: "点" },
  { key: "ih_basis", name: "IH股指期货基差", value: -41.41, unit: "点" },
  { key: "im_basis", name: "IM股指期货基差", value: -100.47, unit: "点" },
  { key: "ic_discount_rate", name: "IC贴水率", value: -1.509, unit: "%" },
  { key: "im_discount_rate", name: "IM贴水率", value: -1.431, unit: "%" },
  { key: "etf_share_change", name: "宽基 ETF 份额变化", value: -12.4, unit: "亿份" },
  { key: "southbound_net_buy", name: "南向资金净买入", value: 45.6, unit: "亿元" },
  // 产业资本待接 S3.2：没有任何行（不是 0）
  {
    key: "industrial_capital_net_change",
    name: "产业资本净增减持",
    value: null,
    unit: "亿元",
    extra: { missing_reason: "upstream_dependency_pending" },
  },
  {
    key: "northbound_holding_quarterly",
    name: "陆股通持仓（季度）",
    value: null,
    unit: "万股",
    freq: "quarterly",
    extra: { missing_reason: "not_ingested_page_pending" },
  },
  { key: "erp_10y", name: "股债收益差（10年期）", value: 3.21, unit: "%" },
  { key: "pe_csi300", name: "沪深300市盈率（整体法）", value: 12.85, unit: "倍" },
  { key: "cn_treasury_yield_10y", name: "10年期国债收益率", value: 2.15, unit: "%" },
  { key: "new_fund_share", name: "新成立基金募集份额", value: 320.5, unit: "亿元", freq: "monthly" },
  {
    key: "public_fund_equity_position",
    name: "公募基金仓位（股票型基金·总仓位，背景参考）",
    value: 84.2,
    unit: "%",
    freq: "weekly",
  },
];

function metricRow(seed: MetricSeed) {
  const { extra, key, ...rest } = seed;
  return {
    metric_key: key,
    tier: "derived",
    source: "local_calc",
    freq: "daily",
    period: "2026-10-12",
    as_of: "2026-10-12",
    as_of_source: "published_at",
    published_at: "2026-10-12T15:00:00+08:00",
    caliber_version: "s1.13-v1",
    caliber_version_registered: "s1.13-v1",
    scope_key: "market",
    delta: null,
    warn: null,
    warn_severity: null,
    spark: null,
    description: "",
    missing_reason: null,
    coverage: null,
    ...rest,
    ...(extra ?? {}),
  };
}

/** `GET /api/v1/market/sentiment/trends`：后端已算好的三个分位（各带自己的窗口）。 */
const TRENDS = {
  as_of: "2026-10-12",
  as_of_quality: "complete",
  window: { days: 250, from: "2025-10-08", to: "2026-10-12", samples: 243 },
  series: [
    {
      trade_date: "2026-10-09",
      zt_count: 68,
      dt_count: 2,
      zb_count: 20,
      broken_rate: 0.2941,
      promo_1to2: 0.18,
      max_streak: 5,
    },
    {
      trade_date: "2026-10-12",
      zt_count: 75,
      dt_count: 1,
      zb_count: 39,
      broken_rate: 0.3421,
      promo_1to2: 0.1585,
      max_streak: 4,
    },
  ],
  percentile: {
    zt_count: {
      field: "zt_count",
      sample_count: 243,
      from: "2025-10-08",
      to: "2026-10-12",
      p0: 12,
      p10: 30,
      p25: 48,
      p50: 66,
      p75: 82,
      p90: 105,
      p100: 143,
    },
    broken_rate: {
      field: "broken_rate",
      sample_count: 241,
      from: "2025-10-08",
      to: "2026-10-12",
      p0: 0.08,
      p10: 0.14,
      p25: 0.21,
      p50: 0.29,
      p75: 0.38,
      p90: 0.47,
      p100: 0.62,
    },
    promo_1to2: {
      field: "promo_1to2",
      sample_count: 238,
      from: "2025-10-08",
      to: "2026-10-12",
      p0: 0.05,
      p10: 0.09,
      p25: 0.14,
      p50: 0.2,
      p75: 0.27,
      p90: 0.35,
      p100: 0.5,
    },
  },
  streak: { zt_above_median_days: 12 },
  insufficient_history: false,
};

/** 统一装好已登录会话 + 三个读面（单个用例可覆盖其中任意一个）。 */
async function mockStack(
  page: Page,
  overrides: { composite?: unknown; metrics?: unknown; trends?: unknown } = {},
) {
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(USER),
    }),
  );
  await page.route("**/api/v1/market/sentiment/composite*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overrides.composite ?? COMPOSITE),
    }),
  );
  await page.route("**/api/v1/metrics/latest*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overrides.metrics ?? METRIC_SEEDS.map(metricRow)),
    }),
  );
  await page.route("**/api/v1/market/sentiment/trends*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(overrides.trends ?? TRENDS),
    }),
  );
}

test("hero：合成值 + 分位轨 + 「N/11 个成分指标 · 生效权重」必须上屏", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  // 合成值（32.8157 → 32.82 分）
  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("32.82");
  // 分位与极值带内的标记
  await expect(page.getByTestId("sentiment-composite-percentile")).toContainText("历史分位 5.2");
  await expect(page.getByTestId("sentiment-composite-rail")).toBeVisible();
  await expect(page.getByTestId("sentiment-composite-marker")).toBeVisible();
  // Ruling 66 的硬要求：最新值的"薄"必须可见
  await expect(page.getByTestId("sentiment-composite-legs-line")).toContainText(
    "本日由 3/11 个成分指标合成 · 生效权重 0.23",
  );
  await expect(page.getByTestId("sentiment-composite-legs-line")).toContainText("（下限 0.20）");
  // window / sample_count 来自 API
  await expect(page.getByTestId("sentiment-hero")).toContainText("分位窗口 1260 个交易日");
  await expect(page.getByTestId("sentiment-hero")).toContainText("有效样本 1260");
  // 页头一句话状态（不是 · 拼接清单）
  await expect(page.getByTestId("sentiment-state")).toContainText("当日由 3/11 个成分指标合成");
});

test("新鲜度：stale=true 必须明写滞后几天 + 日历期望日（不得把期望日说成「已齐备」）", async ({
  page,
}) => {
  await mockStack(page, { composite: COMPOSITE_STALE });
  await page.goto("/sentiment");

  // 读者在读到那个大数字**之前**就得知道它是哪一天的值
  const staleLine = page.getByTestId("sentiment-composite-stale");
  await expect(staleLine).toBeVisible();
  await expect(staleLine).toContainText("数据滞后 3 个交易日");
  // 2026-10-19 修文案：这里原来写「已有齐备数据到」——那是日历期望日，不是齐备日
  await expect(staleLine).toContainText("日历期望日");
  await expect(staleLine).not.toContainText("已有齐备数据到");
  // 日期必须走 formatCnDate（「10月14日」），**不得**出现原始 ISO（见 noRawIso.spec.ts）
  await expect(staleLine).toContainText("10月14日");
  await expect(staleLine).toContainText("10月9日");
  await expect(staleLine).not.toContainText("2026-10-");
  // 落后的日子照常披露参与成分指标数（10/11 · 0.88），不得因为"滞后"就把数值藏起来
  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("44.51");
  // 页头一句话也要带上落后信息（否则会被读成"最新交易日"）
  await expect(page.getByTestId("sentiment-state")).toContainText("落后 3 个交易日");
  await expect(page.getByTestId("sentiment-composite-legs-line")).toContainText(
    "本日由 10/11 个成分指标合成",
  );
  // 没有 latest_withheld（老后端）⇒ 不编造"当日为什么没上屏"
  await expect(page.getByTestId("sentiment-composite-withheld")).toHaveCount(0);
});

test("新鲜度：当日被闸门拦下时必须交代 8/11 · 0.72 · 未达阈值与缺腿类型", async ({ page }) => {
  await mockStack(page, { composite: COMPOSITE_STALE_WITH_HELD });
  await page.goto("/sentiment");

  const withheld = page.getByTestId("sentiment-composite-withheld");
  await expect(withheld).toBeVisible();
  await expect(withheld).toContainText("10月19日");
  await expect(withheld).toContainText("8/11 条腿");
  await expect(withheld).toContainText("0.72");
  await expect(withheld).toContainText("未达 0.80 发布阈值");
  await expect(withheld).toContainText("缺 3 条：2 条 T+1 披露、1 条源侧滞后");
  await expect(withheld).not.toContainText("2026-10-");
  // 页头一句话同样要带真实原因（而不是"已齐备到 10-19"）
  const state = page.getByTestId("sentiment-state");
  await expect(state).toContainText("日历期望日 10月19日");
  await expect(state).toContainText("当日仅 8/11 条腿");
  await expect(state).toContainText("未达 0.80 发布阈值");
});

test("新鲜度：stale=null（判不出）不得沉默 —— 沉默会被读成「新鲜」", async ({ page }) => {
  await mockStack(page, { composite: COMPOSITE_FRESHNESS_UNKNOWN });
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-freshness-unknown")).toContainText(
    "无法判定新鲜度",
  );
  await expect(page.getByTestId("sentiment-state")).toContainText("新鲜度无法判定");
  // 判不出 ≠ 滞后：不显示"滞后 N 个交易日"那条
  await expect(page.getByTestId("sentiment-composite-stale")).toHaveCount(0);
  // 主数据照常上屏
  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("32.82");
});

test("新鲜度：stale=false（追平）不加任何提示 —— 正常态不制造噪声", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("32.82");
  await expect(page.getByTestId("sentiment-composite-stale")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-freshness-unknown")).toHaveCount(0);
  // 临时值同一条纪律：provisional=false（终值）不制造噪声
  await expect(page.getByTestId("sentiment-composite-provisional")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-provisional-unknown")).toHaveCount(0);
});

test("临时值：provisional=true 必须明写「待上游披露后重算、届时可能变化」且不承诺时点", async ({
  page,
}) => {
  await mockStack(page, { composite: COMPOSITE_PROVISIONAL });
  await page.goto("/sentiment");

  const line = page.getByTestId("sentiment-composite-provisional");
  await expect(line).toBeVisible();
  await expect(line).toContainText("缺融资融券");
  await expect(line).toContainText("T+1");
  await expect(line).toContainText("重算");
  await expect(line).toContainText("可能变化");
  // 上游披露可能超过一天 ⇒ 不得承诺"次日"/"明天"这类具体时点
  await expect(line).not.toContainText("次日");
  await expect(line).not.toContainText("明日");
  // 页头一句话同档（true ⇒ 临时值；**不**写成"无法判定"）
  await expect(page.getByTestId("sentiment-state")).toContainText("本期为临时值");
  await expect(page.getByTestId("sentiment-state")).not.toContainText("无法判定本期是否为临时值");
  // 两档互斥：判不出那档不得同时上屏
  await expect(page.getByTestId("sentiment-composite-provisional-unknown")).toHaveCount(0);
});

test("临时值：provisional=null（判不出）不得沉默，更不得塌成「终值」", async ({ page }) => {
  await mockStack(page, { composite: COMPOSITE_PROVISIONAL_UNKNOWN });
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-provisional-unknown")).toContainText(
    "无法判定本期是否为临时值",
  );
  // null ≠ true：不显示"会被重算"那档
  await expect(page.getByTestId("sentiment-composite-provisional")).toHaveCount(0);
  // 主数据照常上屏
  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("32.82");
});

test("样本不足：显示「样本不足」，不给分位数字、不画标记", async ({ page }) => {
  await mockStack(page, { composite: COMPOSITE_INSUFFICIENT });
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-insufficient")).toContainText("样本不足");
  await expect(page.getByTestId("sentiment-composite-percentile")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-marker")).toHaveCount(0);
  // 值照常显示（"算不出分位"与"值为 0"是两件事）
  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("32.82");
  await expect(page.getByTestId("sentiment-state")).toContainText("历史样本不足");
});

test("当期 value 为 NULL（样本够）：说「没有可定位的今日值」，不冒充「样本不足」", async ({ page }) => {
  // 真实后端两档"没有分位"：① 样本 < 60；② 当期 value=NULL（样本够，但没有今日值可分位）。
  // 后者若被写成「样本不足」就是另一种谎 —— 此用例把两档文案钉开。
  await mockStack(page, {
    composite: { ...COMPOSITE, value: null, percentile: null, insufficient_history: false },
  });
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-value")).toHaveText("--");
  await expect(page.getByTestId("sentiment-composite-no-position")).toContainText("当期 value 为 NULL");
  await expect(page.getByTestId("sentiment-composite-insufficient")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-percentile")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-marker")).toHaveCount(0);
});

test("旧口径行缺 participants / n_constituents：不印「0/0 个成分指标」，明说未登记", async ({
  page,
}) => {
  await mockStack(page, {
    composite: { ...COMPOSITE, legs: null, leg_count: 0, total_legs: 0, effective_weight: null },
  });
  await page.goto("/sentiment");

  await expect(page.getByTestId("sentiment-composite-legs-unregistered")).toContainText(
    "未登记参与成分指标",
  );
  await expect(page.getByTestId("sentiment-composite-legs-line")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-hero")).not.toContainText("0/0 个成分指标");
});

test("端点未就绪（404）：hero 如实说暂不可得，不显示占位数字", async ({ page }) => {  await page.route("**/auth/session", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(USER) }),
  );
  await page.route("**/api/v1/market/sentiment/composite*", (route) =>
    route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Not Found" }) }),
  );
  await page.route("**/api/v1/metrics/latest*", (route) =>
    route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify(METRIC_SEEDS.map(metricRow)),
    }),
  );
  await page.route("**/api/v1/market/sentiment/trends*", (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(TRENDS) }),
  );

  await page.goto("/sentiment");
  await expect(page.getByTestId("sentiment-composite-unavailable")).toContainText("暂不可得");
  await expect(page.getByTestId("sentiment-composite-value")).toHaveCount(0);
  await expect(page.getByTestId("sentiment-composite-marker")).toHaveCount(0);
  // 邻区不受影响：模块面板照常读数
  await expect(page.getByTestId("sentiment-metric-advance_decline_ratio")).toContainText("2.14");
});

test("两个未落地模块就地给结论（不是空白、也没有空图表）", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  // ⑩ 情绪预警与预判：依赖 S2.2 信号引擎
  const alert = page.getByTestId("sentiment-unbuilt-alert");
  await expect(alert).toBeVisible();
  await expect(alert).toContainText("S2.2");
  await expect(alert).toContainText("未落地");
  await expect(alert).toContainText("signals / alerts");
  // 该模块没有任何表格（不留空图表）
  await expect(page.locator("#sentiment-module-alert table")).toHaveCount(0);

  // ⑧ 散户舆情：spec 标 superseded
  const retail = page.getByTestId("sentiment-unbuilt-retail");
  await expect(retail).toBeVisible();
  await expect(retail).toContainText("S1.14");
  await expect(retail).toContainText("superseded");
  await expect(page.locator("#sentiment-module-retail table")).toHaveCount(0);
});

test("口径注齐备（S1.9 / S1.10×2 / S1.11 / S1.12×2 / S1.13 / 缺失纪律）", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  const required: Array<[string, string]> = [
    ["turnover", "自由流通股本口径"],
    ["basis-sign", "基差 = 期货 − 现货"],
    ["qvix-lag", "第三方编制"],
    ["pe-caliber", "整体法"],
    ["fund-lag", "口径不可复算"],
    ["lower-bound", "下界"],
    ["composite-weights", "权重表与方法公开可查"],
    ["missing-none", "绝不落 0"],
  ];
  for (const [id, snippet] of required) {
    const note = page.getByTestId(`sentiment-caliber-${id}`);
    await expect(note, `口径注 ${id} 必须存在`).toBeVisible();
    await expect(note, `口径注 ${id} 的原文`).toContainText(snippet);
  }
  // 8 条（批准稿要求 6–8 条），且都是行内注（不是可展开的通用组件）
  await expect(page.locator('[data-testid^="sentiment-caliber-"]')).toHaveCount(8);
});

test("分位只出现在后端给了分位的行上，且每个 chip 自带样本窗口", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  // 给了分位的三行（两条挂指标 + 一条仅分位）
  const brokenRow = page.getByTestId("sentiment-metric-limit_up_break_ratio");
  await expect(brokenRow.locator('[data-testid="sentiment-pct-broken_rate"]')).toContainText(
    "近 241 交易日分位",
  );
  await expect(brokenRow.locator('[data-testid="sentiment-pct-broken_rate"]')).toContainText("P50 29.00%");
  const promoRow = page.getByTestId("sentiment-metric-first_board_promotion_ratio");
  await expect(promoRow.locator('[data-testid="sentiment-pct-promo_1to2"]')).toContainText("P50 20.00%");
  await expect(page.getByTestId("sentiment-percentile-row-zt_count")).toContainText("75家");

  // 后端没给分位的行：行内不得出现任何分位 chip（前端不许自己算）
  await expect(page.locator('[data-testid="sentiment-metric-advance_decline_ratio"] .sent-pct')).toHaveCount(0);
  await expect(page.locator('[data-testid="sentiment-metric-max_consecutive_limit_up"] .sent-pct')).toHaveCount(0);
  await expect(page.locator('[data-testid="sentiment-metric-erp_10y"] .sent-pct')).toHaveCount(0);
});

test("缺失一律 -- + missing_reason（绝不显示 0）", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  const industrial = page.getByTestId("sentiment-metric-industrial_capital_net_change");
  await expect(industrial.locator(".sent-value")).toHaveText("--");
  await expect(page.getByTestId("sentiment-missing-industrial_capital_net_change")).toContainText(
    "upstream_dependency_pending",
  );
  await expect(industrial).not.toContainText("0.00");

  const qvix = page.getByTestId("sentiment-metric-qvix_50etf");
  await expect(qvix.locator(".sent-value")).toHaveText("--");
  await expect(page.getByTestId("sentiment-missing-qvix_50etf")).toContainText("source_not_updated");

  const northbound = page.getByTestId("sentiment-metric-northbound_holding_quarterly");
  await expect(northbound.locator(".sent-value")).toHaveText("--");
  await expect(page.getByTestId("sentiment-missing-northbound_holding_quarterly")).toContainText(
    "not_ingested_page_pending",
  );
});

test("没有 Tab（模块索引 + 单列滚动），索引覆盖全部 10 个模块", async ({ page }) => {
  await mockStack(page);
  await page.goto("/sentiment");

  await expect(page.locator(".ant-tabs")).toHaveCount(0);
  const index = page.getByTestId("sentiment-module-index");
  await expect(index).toBeVisible();
  // 索引每一项都指向页面上真实存在的锚点
  const hrefs = await index.locator("a").evaluateAll((els) =>
    els.map((el) => (el as HTMLAnchorElement).getAttribute("href") ?? ""),
  );
  expect(hrefs).toHaveLength(10);
  for (const href of hrefs) {
    await expect(page.locator(href)).toHaveCount(1);
  }
});

test("匿名访问 /sentiment 走既有 RequireAuth 重定向（本页消费受保护读面）", async ({ page }) => {
  await page.route("**/auth/session", (route) =>
    route.fulfill({
      status: 401,
      contentType: "application/json",
      body: JSON.stringify({ code: "AUTH_UNAUTHORIZED", message: "未登录或会话已过期" }),
    }),
  );
  await page.goto("/sentiment");
  await page.waitForURL(/\/login\?returnTo=%2Fsentiment/);
  // 品牌字标统一为 `StockBot`（Phase 4 §5.1）；用 `.ant-typography` 容器收窄作用域，
  // 避免中文重文案页面上 `getByText` 撞 strict mode（计划 §1「新断言必须绕开的两个已知坑」）
  await expect(page.locator("h3.ant-typography").filter({ hasText: "StockBot 投研工作台" })).toHaveText(
    /StockBot 投研工作台/,
  );
  await expect(page.getByRole("button", { name: "登 录" })).toBeVisible();
  // 未登录不得渲染任何模块面板
  await expect(page.getByTestId("sentiment-module-index")).toHaveCount(0);
});
