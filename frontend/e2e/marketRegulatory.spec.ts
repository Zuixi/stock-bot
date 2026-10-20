import { expect, test, type Page } from "@playwright/test";

/**
 * `/market/regulatory` 监管异动雷达 E2E（2026-10-20）。
 *
 * **这里只测别层测不到的东西**。口径数学（逐日求和、不重叠窗口、阈值分档、触发价）
 * 由 `backend/tests/test_regulatory_anomaly.py`（37 例）覆盖，编排层（候选集、停牌补位、
 * 缺限价降级）由 `test_regulatory_anomaly_service.py`（4 例）覆盖，探测器由
 * `test_triage_detectors.py` 覆盖。再在 e2e 里重算一遍口径只是把单测抄成慢版。
 *
 * 留给本文件的是**只有渲染层会静默出错的四类**：
 *
 *  1. **线上格式契约**：mock 的是后端**原始 snake_case** payload，
 *     断言 `shared/api/regulatory.ts` 的映射真的落到页面上。
 *     字段改名/拼错在单测里全绿（后端自己的 model 自洽），在页面上表现为
 *     `--` / 0 —— 只有这条能红。
 *  2. **反误导纪律**：`dev3_pct = null`（停牌/次新）必须渲染「窗口缺行」，
 *     不得 `?? 0` 成 `0.00%`；`triggered` 必须不给明日触发价（持平也触发，给价格是误导）。
 *     `?? 0` 是这类页面的默认写法，改一行注释看不出来，只有渲染断言能守。
 *  3. **口径由 payload 下发**：阈值表/失效场景喂一个真实世界里不存在的板块名与阈值，
 *     页面必须原样渲染。前端哪天把常量表复制回去（"顺手写死更省事"），
 *     页面照常绿，本用例红。
 *  4. **降级不静默**：`degraded_reason` 必须显式上屏，不得回落成「暂无数据」。
 *
 * 全部 mock（不 mock 就只有 `--`）。与 `marketPanorama.spec.ts` 那类打活栈的用例
 * 分工不同：那个守「数字是不是真的」，这个守「数字有没有被渲染坏」。
 */

/** 后端读面原始线格式（snake_case）——与 `RegulatoryRadarOut` 逐字段对齐。 */
interface BackendItem {
  symbol: string;
  name: string;
  board: string;
  is_st: boolean;
  direction: "up" | "down";
  status: "triggered" | "near" | "watch";
  close: number;
  day_pct: number | null;
  threshold_pct: number;
  dev3_pct: number | null;
  dev10_pct: number | null;
  dev30_pct: number | null;
  events_10d: number;
  severe_flags: string[];
  streak: number | null;
  days_span: number | null;
  boards_in_window: number | null;
  limit_up: boolean;
  limit_down: boolean;
  benchmark_code: string;
  benchmark_name: string;
  next_trigger: {
    price: number;
    needed_pct: number;
    partial_pct: number;
    limit_pct: number;
    feasible: boolean;
  } | null;
}

interface BackendRadar {
  as_of: string | null;
  as_of_quality: "complete" | "partial" | "fallback" | "requested";
  stale_days: number | null;
  degraded_reason: string | null;
  summary: {
    up_triggered: number;
    up_near: number;
    down_triggered: number;
    down_near: number;
    severe: number;
    limit_up: number;
    limit_down: number;
  };
  items: BackendItem[];
  history: Array<{
    trade_date: string;
    items: Array<{
      symbol: string;
      name: string;
      board: string;
      direction: "up" | "down";
      dev3_pct: number;
      threshold_pct: number;
    }>;
  }>;
  turnover_only: Array<{
    symbol: string;
    name: string;
    board: string;
    is_st: boolean;
    close: number;
    day_pct: number | null;
    turnover_3d_pct: number;
    turnover_3d_avg_pct: number;
    turnover_prev5_avg_pct: number;
    turnover_ratio: number;
  }>;
  turnover_available: boolean;
  turnover_as_of: string | null;
  thresholds: Array<{
    board: string;
    exchange: string;
    threshold_pct: number;
    st_threshold_pct: number | null;
    price_limit_pct: number;
    st_price_limit_pct: number | null;
    benchmark_code: string;
    benchmark_name: string;
  }>;
  caveats: string[];
}

/** 一条基准 item，逐字段可覆写；默认是「主板涨向已达线」。 */
function item(over: Partial<BackendItem> = {}): BackendItem {
  return {
    symbol: "600519",
    name: "贵州茅台",
    board: "主板",
    is_st: false,
    direction: "up",
    status: "triggered",
    close: 18.5,
    day_pct: 9.98,
    threshold_pct: 20,
    dev3_pct: 32.45,
    dev10_pct: 41.2,
    dev30_pct: 55.8,
    events_10d: 1,
    severe_flags: [],
    streak: 6,
    days_span: 10,
    boards_in_window: 6,
    limit_up: true,
    limit_down: false,
    benchmark_code: "000001.SH",
    benchmark_name: "上证综指",
    next_trigger: null,
    ...over,
  };
}

function radar(over: Partial<BackendRadar> = {}): BackendRadar {
  return {
    as_of: "2025-10-20",
    as_of_quality: "complete",
    stale_days: 0,
    degraded_reason: null,
    summary: {
      up_triggered: 0,
      up_near: 0,
      down_triggered: 0,
      down_near: 0,
      severe: 0,
      limit_up: 0,
      limit_down: 0,
    },
    items: [],
    history: [],
    turnover_only: [],
    turnover_available: true,
    turnover_as_of: "2025-10-20",
    // 真实阈值表由后端下发；这里故意用**页面不该知道的**板块名与数值，
    // 用来证明前端没有第二份常量表（写死的那份会渲染 20/30/40，不会渲染 ±55%）。
    thresholds: [
      {
        board: "测试主板",
        exchange: "Shanghai_Stocks",
        threshold_pct: 55,
        st_threshold_pct: 12,
        price_limit_pct: 10,
        st_price_limit_pct: 5,
        benchmark_code: "000001.SH",
        benchmark_name: "上证综指",
      },
    ],
    caveats: ["这条失效场景由后端下发：前端不得复制一份会漂移的常量表。"],
    ...over,
  };
}

async function mockRadar(page: Page, payload: BackendRadar) {
  await page.route("**/api/v1/market/regulatory-anomalies*", (route) =>
    route.fulfill({ json: payload })
  );
}

const kpi = (page: Page, label: string) => page.locator(".reg-kpi", { hasText: label });
const ladder = (page: Page) => page.getByTestId("section-今日梯队");
const row = (page: Page, text: string) => ladder(page).locator("tr.ant-table-row", { hasText: text });

test("完整 payload 上屏：映射、KPI、梯队、明日触发、历史、阈值全部来自后端", async ({ page }) => {
  await mockRadar(
    page,
    radar({
      summary: {
        up_triggered: 2,
        up_near: 1,
        down_triggered: 1,
        down_near: 0,
        severe: 1,
        limit_up: 4,
        limit_down: 1,
      },
      items: [
        // 涨向已达线：严重异常也命中
        item({ symbol: "600519", name: "贵州茅台", severe_flags: ["10日3次异动"] }),
        // ST：阈值降档到 12%（主板风险警示股 5% 限幅）——ST 标记缺席会把 13.20% 读成
        // 「刚过 20% 的一半」，而不是「已经越过 12% 线」，严重度判断完全相反
        item({
          symbol: "600856",
          name: "ST中天",
          is_st: true,
          threshold_pct: 12,
          dev3_pct: 13.2,
          close: 1.26,
          streak: 3,
          boards_in_window: 3,
          day_pct: 4.96,
        }),
        // 涨向临近：明日再涨 7.85% 触发
        item({
          symbol: "300750",
          name: "宁德时代",
          board: "创业板",
          status: "near",
          threshold_pct: 30,
          dev3_pct: 26.1,
          close: 19.8,
          streak: null,
          boards_in_window: 2,
          limit_up: false,
          benchmark_code: "399006.SZ",
          benchmark_name: "创业板指",
          next_trigger: {
            price: 21.35,
            needed_pct: 7.85,
            partial_pct: 18.25,
            limit_pct: 20,
            feasible: true,
          },
        }),
        // 涨向观察（停牌补位的梯队股）：3 日窗口缺行
        item({
          symbol: "600825",
          name: "新华传媒",
          status: "watch",
          dev3_pct: null,
          streak: null,
          boards_in_window: 6,
          days_span: 10,
          limit_up: false,
        }),
        // 跌向
        item({
          symbol: "000001",
          name: "平安银行",
          direction: "down",
          dev3_pct: -22.1,
          streak: null,
          boards_in_window: null,
          limit_up: false,
          limit_down: true,
        }),
      ],
      history: [
        {
          trade_date: "2025-10-17",
          items: [
            {
              symbol: "600519",
              name: "贵州茅台",
              board: "主板",
              direction: "up",
              dev3_pct: 32.45,
              threshold_pct: 20,
            },
          ],
        },
      ],
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  await expect(ladder(page)).toBeVisible({ timeout: 15_000 });

  // 快照口径 + **口径边界必须常显**（价格口径 + 换手率旁路）。
  // 没这句，投资人会以为一个数字就是交易所认定的全部条件
  await expect(page.locator(".reg-quality")).toHaveText("收盘");
  // 日期行不得再写死「收盘」（与徽标叠成「收盘收盘」，2026-10-21 容器实测抓到）
  await expect(page.locator(".reg-asof")).not.toContainText("收盘收盘");
  // 陈旧度为 0 → 不加「N 天前」徽标（当天数据不该喊"0 天前"）
  await expect(page.locator(".reg-asof")).not.toContainText("天前");
  await expect(page.locator(".reg-hero")).toContainText("换手率旁路");

  // KPI：六个数字按 summary 渲染（snake_case → camelCase 映射的落地证据）
  await expect(kpi(page, "涨向已达线").locator(".reg-kpi__value")).toHaveText("2");
  await expect(kpi(page, "涨向临近").locator(".reg-kpi__value")).toHaveText("1");
  await expect(kpi(page, "跌向已达线").locator(".reg-kpi__value")).toHaveText("1");
  await expect(kpi(page, "严重异常命中").locator(".reg-kpi__value")).toHaveText("1");
  await expect(kpi(page, "今日涨停").locator(".reg-kpi__value")).toHaveText("4");
  await expect(kpi(page, "今日跌停").locator(".reg-kpi__value")).toHaveText("1");

  // 梯队表：涨向 4 只，跌向那只**不得串味**进来；高亮行数 == KPI「涨向已达线」
  // （KPI 与表体分头计算时最坏的情况：头条说 0 只已达线，表里躺着 2 行红的）
  // `tbody tr` 会多算 antd 的 `ant-table-measure-row`（列宽测量行，不渲染）；用 `.ant-table-row`
  // 才是数据行。这里没用设计令牌类名，是 antd 自己给数据行打的稳定钩子。
  await expect(ladder(page).locator("tr.ant-table-row")).toHaveCount(4);
  await expect(ladder(page).locator("tr.reg-row--hot")).toHaveCount(2);
  await expect(ladder(page)).not.toContainText("平安银行");

  // 3 日偏离 / 阈值：两个数字都要在（只渲染偏离、把阈值写死也算坏）
  await expect(row(page, "600519")).toContainText("32.45%");
  await expect(row(page, "600519")).toContainText("20%");
  // 严重异常的 flags 原样上屏
  await expect(row(page, "600519")).toContainText("10日3次异动");
  // 板数：有 streak 用连板，只有 boards_in_window 用「N 天 M 板」
  await expect(row(page, "600519")).toContainText("6 连板");
  await expect(row(page, "600825")).toContainText("10 天 6 板");

  // ST 降档：标记与 12% 阈值都必须在行里（见上方 ST 数据的理由）
  await expect(row(page, "600856")).toContainText("· ST");
  await expect(row(page, "600856")).toContainText("13.20%");
  await expect(row(page, "600856")).toContainText("12%");

  // 个股链接指向**这一只**（链接串号是投资人看错公司的典型路径）
  await expect(row(page, "600519").getByRole("link", { name: "贵州茅台" })).toHaveAttribute(
    "href",
    "/company/600519"
  );

  // 明日触发价必须带方向与价格（needed_pct 7.85 直出，不重算）
  await expect(row(page, "300750")).toContainText("≥ 21.35");
  await expect(row(page, "300750")).toContainText("7.85%");

  // 跌向 tab 有独立数据，且不显示涨向的票
  // 不用 `getByRole("radio")`：antd Segmented 的 input 是 opacity:0 的隐藏控件，
  // role 能解析但 `click()` 永远等不到 visible。点它可见的 label 容器。
  await ladder(page).locator(".ant-segmented-item", { hasText: "跌向" }).click();
  await expect(ladder(page).locator("tr.ant-table-row")).toHaveCount(1);
  await expect(ladder(page)).toContainText("平安银行");
  await expect(ladder(page)).not.toContainText("贵州茅台");
  // 跌向偏离必须带负号：符号丢了就成了「涨了 22.10%」，方向反了
  await expect(row(page, "000001")).toContainText("-22.10%");

  // 明日触发预告：只收「涨向 + 未触发 + 需涨 > 0」，故只有 300750
  const preview = page.getByTestId("section-明日触发预告");
  await expect(preview).toContainText("宁德时代");
  await expect(preview).toContainText("21.35");
  await expect(preview).not.toContainText("贵州茅台");

  // 历史：首次越过阈值日
  await expect(page.locator(".reg-history")).toContainText("贵州茅台");
  await expect(page.locator(".reg-history")).toContainText("+32.45%");

  // 口径区：阈值表与失效场景**原样来自 payload**
  const docs = page.getByTestId("section-口径与已知失效场景");
  await expect(docs).toContainText("测试主板");
  await expect(docs).toContainText("±55%");
  await expect(docs).toContainText("这条失效场景由后端下发");
});

test("反误导纪律：null ≠ 0、已达线不给触发价、持平即触发、限幅不可达", async ({ page }) => {
  await mockRadar(
    page,
    radar({
      summary: {
        up_triggered: 1,
        up_near: 3,
        down_triggered: 0,
        down_near: 0,
        severe: 0,
        limit_up: 1,
        limit_down: 0,
      },
      items: [
        // 停牌/次新：dev3 为 null，必须渲染「窗口缺行」，绝不能 `?? 0` 成 0.00%
        item({ symbol: "600825", name: "新华传媒", status: "watch", dev3_pct: null, streak: null }),
        // 已达线：next_trigger 为 null（后端已置空），页面只许说「已触发」
        item({ symbol: "600519", name: "贵州茅台", status: "triggered", next_trigger: null }),
        // 持平即触发：最近两日偏离已够，明日不动也越线
        item({
          symbol: "000002",
          name: "万科A",
          status: "near",
          dev3_pct: 19.0,
          next_trigger: { price: 9.1, needed_pct: 0, partial_pct: 20, limit_pct: 10, feasible: true },
        }),
        // 所需幅度超过明日限幅 → 说不出可达价格
        item({
          symbol: "000003",
          name: "国农科技",
          status: "near",
          dev3_pct: 16.4,
          next_trigger: { price: 26.4, needed_pct: 40, partial_pct: -20, limit_pct: 10, feasible: false },
        }),
      ],
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  await expect(ladder(page)).toBeVisible({ timeout: 15_000 });

  // 1) 缺行 ≠ 没有偏离
  await expect(row(page, "600825")).toContainText("--（窗口缺行）");
  await expect(row(page, "600825")).not.toContainText("0.00%");

  // 2) 已达线不给价：一旦给出价格，读起来就是「明日还能再上车」
  await expect(row(page, "600519")).toContainText("已触发");
  await expect(row(page, "600519")).not.toContainText("≥");
  await expect(row(page, "600519")).not.toContainText("持平即触发");

  // 3) 持平即触发（needed_pct = 0 是「明日不动也越线」，不是「还需 0.00%」）
  await expect(row(page, "000002")).toContainText("持平即触发");
  await expect(row(page, "000002")).not.toContainText("需 +0.00%");

  // 4) 越过限幅：明说不可达，不要给一个涨跌停也到不了的价格
  await expect(row(page, "000003")).toContainText("涨跌停也难触发");
  await expect(row(page, "000003")).not.toContainText("26.40");
});

test("降级不静默：degraded_reason 必须显式上屏", async ({ page }) => {
  await mockRadar(
    page,
    radar({
      degraded_reason: "price_limits_missing",
      items: [item({ symbol: "600519", name: "贵州茅台" })],
      summary: {
        up_triggered: 1,
        up_near: 0,
        down_triggered: 0,
        down_near: 0,
        severe: 0,
        limit_up: 1,
        limit_down: 0,
      },
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  await expect(ladder(page)).toBeVisible({ timeout: 15_000 });

  // 有数据也要把降级原因说清楚（"涨停股可能漏列"是这条口径的实际后果）
  await expect(page.locator(".reg-alert")).toContainText("缺少交易所涨跌停价");
  await expect(page.locator(".reg-alert")).toContainText("可能漏列");
});

test("无行情：空态文案说清原因，不是「暂无数据」", async ({ page }) => {
  await mockRadar(
    page,
    radar({ as_of: null, as_of_quality: "partial", degraded_reason: "no_quotes", items: [] })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });

  // 断言的是空态**描述文案**本身（`StateWrapper` 的兜底是「暂无数据」，漏传 degraded 就退化成它）。
  // 不能用 `getByText("暂无数据")` 做负向断言：antd `Empty` 的插画自带同名 accessible name，
  // 恒命中，那条断言永远红且不指向真回归。
  await expect(page.locator(".ant-empty-description")).toHaveText(
    "库里还没有行情数据，无法计算监管异动。",
    { timeout: 15_000 }
  );
});

test("明日触发预告按「还需涨幅」升序：这页的扫描入口不能乱序", async ({ page }) => {
  // 这张表的用途就是「按还差多少排，看谁最接近触发」。顺序错了页面照常好看，
  // 但投资人拿到的优先队列是假的 —— 排序是这张卡唯一的语义。
  const near = (symbol: string, name: string, needed: number) =>
    item({
      symbol,
      name,
      status: "near",
      dev3_pct: 17.5,
      next_trigger: {
        price: 10 + needed / 10,
        needed_pct: needed,
        partial_pct: 20 - needed,
        limit_pct: 10,
        feasible: true,
      },
    });

  await mockRadar(
    page,
    radar({
      summary: {
        up_triggered: 0,
        up_near: 3,
        down_triggered: 0,
        down_near: 0,
        severe: 0,
        limit_up: 0,
        limit_down: 0,
      },
      // 故意按「乱序」给：页面必须自己按 needed_pct 升序排
      items: [
        near("000004", "国华网安", 12.4),
        near("000009", "中国宝安", 3.2),
        near("000021", "深科技", 7.85),
      ],
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  const preview = page.getByTestId("section-明日触发预告");
  await expect(preview).toBeVisible({ timeout: 15_000 });

  const rows = preview.locator("tr.ant-table-row");
  await expect(rows).toHaveCount(3);
  await expect(rows.nth(0)).toContainText("中国宝安");
  await expect(rows.nth(1)).toContainText("深科技");
  await expect(rows.nth(2)).toContainText("国华网安");
});

test("回落口径：as_of_quality=fallback 必须说「按最近完整日」，不得冒充当日收盘", async ({
  page,
}) => {
  // 节后第一分钟 / 上游停更时，as_of 可能落后好几天。若口径徽标还写「收盘」，
  // 读者会把几天前的数据当成当天收盘 —— 这页所有数字都是「当日越线」的陈述。
  await mockRadar(
    page,
    radar({
      as_of: "2025-10-09",
      as_of_quality: "fallback",
      stale_days: 12,
      items: [item({ symbol: "600519", name: "贵州茅台" })],
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  await expect(ladder(page)).toBeVisible({ timeout: 15_000 });

  // 两处同源于 `FreshnessNote.qualityBadgeText`（它自声明为全站单一渲染点）。
  // 曾经 hero 写「按最近完整交易日」、卡片写「按最近完整日」，同屏两种措辞 —— 已统一。
  await expect(page.locator(".reg-quality")).toHaveText("按最近完整日");
  // 断言整行（口径徽标 + 陈旧度徽标是两个 span，逐个 `.section-card__quality` 会撞 strict mode）
  const freshness = page.getByTestId("section-今日梯队").locator(".section-card__asof");
  await expect(freshness).toContainText("按最近完整日");
  // 陈旧度必须说出来：落后 12 天的数据如果只写「按最近完整日」，读者无从发现它不是这两天的
  await expect(freshness).toContainText("12 天前");
  // 真相断言：回落日不得被打扮成当日收盘
  await expect(page.locator(".reg-quality")).not.toContainText("收盘");
});

test("基准指数缺日：KPI 全 0 必须配一句「不可当没有异动读」", async ({ page }) => {
  // `daily_deviation` 在基准指数缺日时返回 None，与「个股停牌」同形。没有这句降级，
  // 整块板子就是「已达线 0 / 临近 0 + 每行窗口缺行」，读者会得出相反的结论。
  await mockRadar(
    page,
    radar({
      degraded_reason: "benchmark_missing",
      items: [item({ symbol: "600825", name: "新华传媒", status: "watch", dev3_pct: null })],
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  await expect(ladder(page)).toBeVisible({ timeout: 15_000 });

  await expect(page.locator(".reg-alert")).toContainText("基准指数日线缺日");
  await expect(page.locator(".reg-alert")).toContainText("不可当“没有异动”读");
  // 表格照常渲染（不是空态），但每行的偏离是「缺行」而不是 0
  await expect(row(page, "600825")).toContainText("--（窗口缺行）");
  await expect(row(page, "600825")).not.toContainText("0.00%");
});

test("仅换手率达标：旁路单独列，且与价格表不重复", async ({ page }) => {
  // 交易所的换手率触发条件是**独立**条件。把它塞进 items 会让「一个数字 = 全部认定条件」
  // 的误读重现；这里守的是「旁路数组原样渲染 + 不与价格表混在一起」。
  await mockRadar(
    page,
    radar({
      items: [item({ symbol: "600519", name: "贵州茅台" })],
      turnover_only: [
        {
          symbol: "603137",
          name: "恒尚节能",
          board: "主板",
          is_st: false,
          close: 26.75,
          day_pct: 6.15,
          turnover_3d_pct: 53.91,
          turnover_3d_avg_pct: 17.97,
          turnover_prev5_avg_pct: 0.32,
          turnover_ratio: 56.02,
        },
      ],
      turnover_available: true,
      turnover_as_of: "2025-10-20",
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  const block = page.getByTestId("reg-turnover-only");
  await expect(block).toBeVisible({ timeout: 15_000 });
  await expect(block).toContainText("仅换手率达标");
  await expect(block).toContainText("恒尚节能");
  await expect(block).toContainText("56×");
  await expect(block.getByRole("link", { name: /恒尚节能/ })).toHaveAttribute(
    "href",
    "/company/603137"
  );
  // 价格表不因旁路多出一行
  await expect(ladder(page).locator("tr.ant-table-row")).toHaveCount(1);
});

test("换手率数据未更新：旁路必须说清原因，不得静默成「没有换手率异动」", async ({
  page,
}) => {
  await mockRadar(
    page,
    radar({
      items: [item({ symbol: "600519", name: "贵州茅台" })],
      turnover_only: [],
      turnover_available: false,
      turnover_as_of: "2025-10-18",
    })
  );

  await page.goto("/market/regulatory", { waitUntil: "domcontentloaded" });
  const missing = page.getByTestId("reg-turnover-missing");
  await expect(missing).toBeVisible({ timeout: 15_000 });
  await expect(missing).toContainText("未更新");
  await expect(missing).toContainText("10月18日");
});

test("筛选/搜索/深链都写进 URL，可分享可回退", async ({ page }) => {
  await mockRadar(
    page,
    radar({
      items: [
        item({ symbol: "600519", name: "贵州茅台" }),
        item({ symbol: "600856", name: "ST中天", is_st: true, threshold_pct: 12, dev3_pct: 13.2 }),
        item({ symbol: "300750", name: "宁德时代", board: "创业板", status: "near", threshold_pct: 30 }),
        item({ symbol: "600825", name: "新华传媒", status: "watch", dev3_pct: null }),
        item({ symbol: "000001", name: "平安银行", direction: "down", dev3_pct: -22.1 }),
      ],
    })
  );

  // 深链：?symbol= 高亮对应行（自选体检「异动雷达」入口带过来的参数）
  await page.goto("/market/regulatory?symbol=600825", { waitUntil: "domcontentloaded" });
  await expect(row(page, "600825")).toHaveClass(/reg-row--focus/, { timeout: 15_000 });

  // 搜索：写进 URL，表格即时收窄
  const search = page.getByLabel("按代码或名称搜索");
  await search.fill("茅台");
  await expect(ladder(page).locator("tr.ant-table-row")).toHaveCount(1);
  await expect(page).toHaveURL(/q=/);
  await expect(row(page, "600519")).toBeVisible();

  // 清空搜索 + 状态筛选：只留已达线（600519 / 600856）
  await search.fill("");
  await ladder(page).locator(".ant-segmented-item", { hasText: "已达线" }).click();
  await expect(ladder(page).locator("tr.ant-table-row")).toHaveCount(2);
  await expect(page).toHaveURL(/status=triggered/);
  await expect(ladder(page)).not.toContainText("宁德时代");
});
