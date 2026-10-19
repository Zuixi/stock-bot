import { test, expect as baseExpect } from "@playwright/test";

/**
 * P0-4 E2E：交易所直连的**请求数证据**（依赖运行中的 docker 栈 + 本 worktree 的 vite dev）。
 *
 * 背景：改造前 `fetchStockEnrichedBySymbol` / `fetchKlineBySymbol` 都按
 * `Shanghai_Stocks → Shenzen_Stocks → Beijing_Stocks` 串行探活，最坏 3 次 HTTP 请求。
 * 改造后按代码前缀先猜一所，因此：
 * - 600519（沪市）第 1 次就命中上交所，**永不**请求深交所；
 * - 000001（深市）第 1 次就命中深交所，**永不**请求上交所 —— 这一条是能区分新旧的
 *   判别性断言（旧实现必定先打上交所，深市代码多付一次 404）。
 *
 * 断言纪律：
 * - `page.on("request")` 必须在 `goto` **之前**注册，否则首个请求会漏记；
 * - 锚点是页面上真实出现的「历史行情」卡片（KlineChart 已挂载 + 名录取到数据），
 *   不是固定 sleep；
 * - 只统计**详情页自己发起的**请求：URL 精确到 `/exchanges/<ex>/stocks/<symbol>`。
 */

const expect = baseExpect.configure({ timeout: 15_000 });

const EXCHANGE_PROBE_RE =
  /\/api\/v1\/exchanges\/(Shanghai_Stocks|Shenzen_Stocks|Beijing_Stocks)\/stocks\/(\d{6})(\/[^?#]*)?(\?|$)/;

interface ProbeRequest {
  exchange: "Shanghai_Stocks" | "Shenzen_Stocks" | "Beijing_Stocks";
  symbol: string;
  path: string;
  url: string;
}

/** 按顺序记录所有命中 `.../exchanges/<ex>/stocks/<6位代码>...` 的请求。 */
function recordExchangesProbes(page: import("@playwright/test").Page, symbol: string): ProbeRequest[] {
  const probes: ProbeRequest[] = [];
  page.on("request", (request) => {
    const match = EXCHANGE_PROBE_RE.exec(request.url());
    if (!match || match[2] !== symbol) return;
    probes.push({
      exchange: match[1] as ProbeRequest["exchange"],
      symbol: match[2],
      path: match[3] ?? "",
      url: request.url(),
    });
  });
  return probes;
}

const isEnriched = (probe: ProbeRequest) => probe.path.endsWith("/enriched");
const isKline = (probe: ProbeRequest) => probe.path.includes("/quotes/daily");

test("沪市个股 600519：enriched 只发 1 次，首个探针命中上交所，从不试探深交所", async ({ page }) => {
  const probes = recordExchangesProbes(page, "600519");

  await page.goto("/stock/600519");
  // 锚点：历史行情卡片出现 = 名录请求已返回、详情页已渲染（K 线请求也已发出）
  await expect(page.locator(".ant-card").filter({ hasText: "历史行情" })).toBeVisible();
  // 让在途请求落地，再统计（避免"断言时还没发出来"造成假绿）
  await page.waitForLoadState("networkidle").catch(() => {});

  const enriched = probes.filter(isEnriched);
  expect(
    enriched.map((probe) => probe.url),
    "详情页 enriched 请求应恰好 1 次（命中即返回，不做串行探活）",
  ).toHaveLength(1);

  // 顺序证据：整个详情页对 600519 的第 1 个探针必须是上交所
  expect(probes.length, "详情页至少应发出 1 个交易所探针").toBeGreaterThan(0);
  expect(probes[0].exchange, `首个探针打到了 ${probes[0].exchange}（期望 Shanghai_Stocks）`).toBe(
    "Shanghai_Stocks",
  );

  // 沪市代码不该出现任何深市请求（无论 enriched 还是 kline）
  const shenzen = probes.filter((probe) => probe.exchange === "Shenzen_Stocks");
  expect(
    shenzen.map((probe) => probe.url),
    "600519 是沪市代码，不应向 Shenzen_Stocks 发任何请求",
  ).toEqual([]);
});

test("深市个股 000001：首个探针命中深交所，kline 只发 1 次（旧实现会先打上交所 404）", async ({
  page,
}) => {
  const probes = recordExchangesProbes(page, "000001");

  await page.goto("/stock/000001");
  await expect(page.locator(".ant-card").filter({ hasText: "历史行情" })).toBeVisible();
  await page.waitForLoadState("networkidle").catch(() => {});

  expect(probes.length, "详情页至少应发出 1 个交易所探针").toBeGreaterThan(0);
  expect(probes[0].exchange, `首个探针打到了 ${probes[0].exchange}（期望 Shenzen_Stocks）`).toBe(
    "Shenzen_Stocks",
  );

  const kline = probes.filter(isKline);
  expect(kline.map((probe) => probe.url), "深市 K 线应 1 次命中，不再串行试探上交所").toHaveLength(1);
  expect(kline[0].exchange).toBe("Shenzen_Stocks");

  // 判别性断言：旧实现的第一发必定是上交所（000001 的 kline 在上交所是 404）
  const shanghai = probes.filter((probe) => probe.exchange === "Shanghai_Stocks");
  expect(
    shanghai.map((probe) => probe.url),
    "000001 是深市代码，不应向 Shanghai_Stocks 发任何请求",
  ).toEqual([]);
});
