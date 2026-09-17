import { expect, test, type Page } from "@playwright/test";
import { INDEX_POLL_INTERVAL_MS, OPEN_POLL_INTERVAL_MS } from "../src/features/market/hooks/useMarketPolling";

/**
 * 市场卡片轮询节奏（Phase 1 Task 9 fix round 1，controller 裁决）。
 *
 * 裁决：**指数卡不得被 A 股时段绑架**。`/market/global-indices` 同时含美股/欧股，
 * 美股时段在 A 股收盘之后；Task 9 让指数卡共用 A 股时段后，收盘即停摆，夜盘整晚不更新。
 * 故：
 * - 指数卡（`GlobalMarketBoard` / `CoreIndexCards` / 首页指数条）→ 常驻 300s，与 A 股状态无关；
 * - A 股按日卡（涨跌分布等）→ 仍随 A 股时段：开市 30s、其余不轮询。
 *
 * 取证方式：`page.clock` 假时钟 + 真实网络请求计数。假时钟只冻 `Date`/定时器，
 * 不发假请求——所以「计数增量」就是前端真实的轮询节奏。分块推进（每块比目标间隔
 * 略长 + Node 侧真实 sleep）是为了让在途请求落地，避免 react-query 的 in-flight dedupe
 * 把连续触发的轮询合并成一次请求（那会低估节奏，把断言测成假绿）。
 */

const expect15s = expect.configure({ timeout: 15_000 });

/** 2026-09-19 是周六，22:00 上海（= 14:00Z）：A 股休市，美股当晚仍在交易。 */
const CLOSED_SATURDAY_22H = new Date("2026-09-19T14:00:00Z");
/** 2026-09-17 是周四，10:00 上海（= 02:00Z）：A 股盘中。 */
const OPEN_THURSDAY_10H = new Date("2026-09-17T02:00:00Z");

const MARKET_API = "/api/v1/market/";
/**
 * 一次取证窗口 = 30 块 × 31s ≈ 930s。
 *
 * 为什么不刚好取 2 个指数周期（600s）：react-query 的 interval 每次**请求落地后**才重新
 * 起算，真实后端延迟会让下一次起算点晚于块边界（后端忙时实测晚 1~2 块）。窗口留出
 * 余量才能把「指数卡在休市时仍在轮询」测成确定性事实，而不是把慢后端测成假红。
 * 也正因为起算点漂移，断言取下界 2（≈≤465s 一次，量级仍是 300s，绝不是 30s/60s）。
 */
const CHUNKS = 30;
const CHUNK_MS = 31_000;

/** Node 侧真实 sleep：`page.waitForTimeout` 在装了假时钟的上下文里语义可疑，不用它。 */
const realSleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

/** 统计本页发往 `/api/v1/market/<x>` 的**真实**请求次数（按端点分桶）。 */
function trackMarketRequests(page: Page): Map<string, number> {
  const counts = new Map<string, number>();
  page.on("request", (req) => {
    const { pathname } = new URL(req.url());
    if (!pathname.startsWith(MARKET_API)) return;
    const endpoint = pathname.slice(MARKET_API.length);
    counts.set(endpoint, (counts.get(endpoint) ?? 0) + 1);
  });
  return counts;
}

/** 进 /market 并让两组卡都挂载、首批请求都落地（避免把首次请求算进轮询增量）。 */
async function mountBothCardGroups(page: Page) {
  await page.goto("/market");
  await expect15s(page.getByRole("tab", { name: "指数总览" })).toBeVisible();
  // 用卡头标题定位：「全球指数」在「数据版图」卡体内也出现（strict mode 会双命中）
  await expect15s(page.locator(".ant-card-head-title").filter({ hasText: "全球指数" }).first()).toBeVisible();

  const distributionLanded = page.waitForResponse((r) => r.url().includes("/api/v1/market/distribution"));
  await page.getByRole("tab", { name: "A股全景" }).click();
  await distributionLanded;
  await expect15s(page.locator(".ant-card").filter({ hasText: "A股涨跌分布" })).toBeVisible();
}

/** 分块推进假时钟，每块后给真实网络留出落地时间。 */
async function advanceInChunks(page: Page, chunks: number, chunkMs: number) {
  for (let i = 0; i < chunks; i += 1) {
    await page.clock.runFor(chunkMs);
    await realSleep(200);
  }
}

test.describe("市场卡片轮询节奏", () => {
  test("休市（周六 22:00）：指数卡仍每 300s 刷新，A 股卡完全不轮询", async ({ page }) => {
    const resp = await page.request.get("/api/v1/market/global-indices");
    test.skip(!resp.ok(), "global-indices 端点不可用，跳过");

    await page.clock.install({ time: CLOSED_SATURDAY_22H });
    const counts = trackMarketRequests(page);
    await mountBothCardGroups(page);

    const indexBefore = counts.get("global-indices") ?? 0;
    const distBefore = counts.get("distribution") ?? 0;
    const sectorsBefore = counts.get("sectors") ?? 0;
    expect(indexBefore).toBeGreaterThan(0);

    await advanceInChunks(page, CHUNKS, CHUNK_MS);
    await realSleep(400); // 事件队列里最后几个请求计数落地

    // 常驻慢轮询：930s 内至少 2 次（含 300s/600s 两个边界），且远慢于盘中节奏
    const indexPolls = counts.get("global-indices")! - indexBefore;
    const distPolls = (counts.get("distribution") ?? 0) - distBefore;
    const sectorsPolls = (counts.get("sectors") ?? 0) - sectorsBefore;
    console.log(`[轮询取证·休市] indexPolls=${indexPolls} distPolls=${distPolls} sectorsPolls=${sectorsPolls}`);
    expect(indexPolls).toBeGreaterThanOrEqual(2);
    expect(indexPolls).toBeLessThanOrEqual(4); // 300s 量级，不是 30s/60s
    // A 股按日卡：status=closed → refetchInterval=false，一次都不再打
    expect(distPolls).toBe(0);
    expect(sectorsPolls).toBe(0);
  });

  test("开市（周四 10:00）：A 股卡按 30s 刷新，指数卡不被带快（仍 300s）", async ({ page }) => {
    const resp = await page.request.get("/api/v1/market/distribution");
    test.skip(!resp.ok(), "distribution 端点不可用，跳过");

    await page.clock.install({ time: OPEN_THURSDAY_10H });
    const counts = trackMarketRequests(page);
    await mountBothCardGroups(page);

    const indexBefore = counts.get("global-indices") ?? 0;
    const distBefore = counts.get("distribution") ?? 0;
    expect(distBefore).toBeGreaterThan(0);

    await advanceInChunks(page, CHUNKS, CHUNK_MS);
    await realSleep(400);

    // A 股卡跟着 30s 节奏跑（930s ≈ 31 次；阈值给 in-flight dedupe / 慢响应留余量）
    const distPolls = counts.get("distribution")! - distBefore;
    const indexPolls = counts.get("global-indices")! - indexBefore;
    console.log(`[轮询取证·开市] indexPolls=${indexPolls} distPolls=${distPolls}`);
    expect(distPolls).toBeGreaterThanOrEqual(15);
    // 指数卡同一窗口内仍是 300s 量级：不被 A 股 30s 节奏带快
    expect(indexPolls).toBeGreaterThanOrEqual(2);
    expect(indexPolls).toBeLessThanOrEqual(4);
    expect(distPolls).toBeGreaterThan(indexPolls * 3);
  });

  test("节奏常量与卡片用法一致（防两处各写一个数字）", async () => {
    // 指数卡常驻慢轮询必须显著慢于盘中节奏，且为整分钟（可读、便于解释）
    expect(INDEX_POLL_INTERVAL_MS).toBe(300_000);
    expect(OPEN_POLL_INTERVAL_MS).toBe(30_000);
    expect(INDEX_POLL_INTERVAL_MS).toBeGreaterThan(OPEN_POLL_INTERVAL_MS);
  });
});
