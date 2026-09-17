import { expect, test, type Locator, type Page } from "@playwright/test";
import { INDEX_POLL_INTERVAL_MS, OPEN_POLL_INTERVAL_MS } from "../src/features/market/hooks/useMarketPolling";

/**
 * 市场卡片轮询节奏（Task 9 fix round 1 裁决：指数卡不得被 A 股时段绑架；
 * fix round 2 裁决：按市场拆 query key）。
 *
 * 现状（两轮裁决叠加后的口径）：
 * - **全球指数盘**（`GlobalMarketBoard` + 首页指数条，key `["market","global-indices"]`）
 *   → 常驻 300s，与 A 股状态无关（夜盘美股仍在交易的时段不能停摆）；
 * - **A股核心指数卡**（`CoreIndexCards`，key `["market","global-indices","cn"]`）
 *   → 随 A 股时段：开市 30s、休市不轮询（它是市场页主 Tab 的 A 股口径卡片，
 *   fix round 1 的「同 key 300s」把盘中它从 30s 拖慢到 300s，是回归）；
 * - **A 股按日卡**（涨跌分布等）→ 同样随 A 股时段：开市 30s、其余不轮询。
 *
 * 取证方式两种，互相独立：
 * 1. **真实网络请求计数**（`page.clock` 假时钟只冻 `Date`/定时器，不发假请求）——
 *    证明同端点聚合节奏；分块推进（每块比目标间隔略长 + Node 侧真实 sleep）是为了让
 *    在途请求落地，避免 react-query 的 in-flight dedupe 把连续触发合并成一次请求而低估节奏。
 * 2. **mock 响应内的自增序号 + 逐卡读取**（`#N` 打在指数名上）——请求计数无法区分
 *    「谁在轮询」（两个 query 打同一个 URL），序号却能：某张卡显示的值变新 = 它自己的
 *    query 真的发了新请求。开市 155s（<300s）窗口是全球卡与 A 股核心卡的**判别性**证据。
 */

const expect15s = expect.configure({ timeout: 15_000 });

/** 2026-09-19 是周六，22:00 上海（= 14:00Z）：A 股休市，美股当晚仍在交易。 */
const CLOSED_SATURDAY_22H = new Date("2026-09-19T14:00:00Z");
/** 2026-09-17 是周四，10:00 上海（= 02:00Z）：A 股盘中。 */
const OPEN_THURSDAY_10H = new Date("2026-09-17T02:00:00Z");

const MARKET_API = "/api/v1/market/";
const GLOBAL_INDICES = "/api/v1/market/global-indices";
/**
 * 一次取证窗口 = 30 块 × 31s ≈ 930s。
 *
 * 为什么不刚好取 2 个指数周期（600s）：react-query 的 interval 每次**请求落地后**才重新
 * 起算，真实后端延迟会让下一次起算点晚于块边界（后端忙时实测晚 1~2 块）。窗口留出
 * 余量才能把「全球盘在休市时仍在轮询」测成确定性事实，而不是把慢后端测成假红。
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

/** 只挂载指数总览 Tab 的两张指数卡（不点 A股全景，避免隐藏面板干扰逐卡读取）。 */
async function mountIndexCards(page: Page) {
  await page.goto("/market");
  await expect15s(page.getByRole("tab", { name: "指数总览" })).toBeVisible();
  await expect15s(boardCard(page)).toBeVisible();
  await expect15s(coreIndexCard(page)).toBeVisible();
}

/** 卡头唯一文案定位：`filter({ has })` 会把内部 locator 重挂到外层元素上。 */
function cardByTitle(page: Page, title: string): Locator {
  return page
    .locator(".ant-card")
    .filter({ has: page.locator(".ant-card-head-title", { hasText: title }) })
    .first();
}
const boardCard = (page: Page) => cardByTitle(page, "全球指数");
const coreIndexCard = (page: Page) => cardByTitle(page, "A股核心指数");

/**
 * 把每张指数卡渲染到的 mock 序号读出来（app 把序号拼在指数名后 → `上证指数#7`）。
 * 卡内 6~N 格里所有指数都打同一个序号，故「卡文本里第一个 `#N`」即该卡当前数据版本。
 */
async function readMarker(card: Locator): Promise<number> {
  const text = await card.innerText();
  const match = text.match(/#(\d+)/);
  expect(match, `卡片应显示带序号的 mock 指数名，实际: ${text.slice(0, 160)}`).not.toBeNull();
  return Number(match![1]);
}

/**
 * 用「实抓的活响应 + 只把序号拼进 name」做 mock：
 * 响应形状仍来自真实后端（手写 mock 只锁得住前端自己的假设），
 * 而每次响应的 `name` 都不同 → 卡片显示的值就是「它自己 query 拿到的第几次响应」。
 */
async function mockIndicesWithSeq(page: Page, rows: Array<Record<string, unknown>>) {
  let seq = 0;
  await page.route(`**${GLOBAL_INDICES}`, async (route) => {
    seq += 1;
    await route.fulfill({ json: rows.map((row) => ({ ...row, name: `${row.name}#${seq}` })) });
  });
}

/** 取活栈原始 payload（snake_case）；端点不可用则整例 skip（仓库既有惯例）。 */
async function liveIndexRows(page: Page): Promise<Array<Record<string, unknown>>> {
  const resp = await page.request.get(GLOBAL_INDICES);
  test.skip(!resp.ok(), "global-indices 端点不可用，跳过");
  return (await resp.json()) as Array<Record<string, unknown>>;
}

/** 分块推进假时钟，每块后给真实网络留出落地时间。 */
async function advanceInChunks(page: Page, chunks: number, chunkMs: number) {
  for (let i = 0; i < chunks; i += 1) {
    await page.clock.runFor(chunkMs);
    await realSleep(200);
  }
}

test.describe("市场卡片轮询节奏", () => {
  test("休市（周六 22:00）：全球指数盘仍每 300s 刷新，A 股卡完全不轮询", async ({ page }) => {
    const resp = await page.request.get(GLOBAL_INDICES);
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

    // 全球盘常驻慢轮询：930s 内至少 2 次（含 300s/600s 两个边界），且远慢于盘中节奏
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

  test("开市（周四 10:00）：A 股卡（含 A股核心指数）按 30s 刷新", async ({ page }) => {
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
    // A股核心指数卡回到 A 股时段节奏 → 该端点在盘中同样每 30s 被打（≈31 次）。
    // 若它被改回 `"global-index"`（fix round 1 的回归）这里只会有 2~4 次。
    expect(indexPolls).toBeGreaterThanOrEqual(15);
    expect(indexPolls).toBeLessThanOrEqual(45); // 上界防跑飞：不是「越快越好」
  });

  test("开市 155s：A股核心指数卡已取到新数据，全球指数卡未到 300s 一次都没刷", async ({ page }) => {
    const rows = await liveIndexRows(page);

    await page.clock.install({ time: OPEN_THURSDAY_10H });
    await mockIndicesWithSeq(page, rows);
    await mountIndexCards(page);
    await expect15s(coreIndexCard(page)).toContainText(/#\d+/);

    const coreBefore = await readMarker(coreIndexCard(page));
    const boardBefore = await readMarker(boardCard(page));

    await advanceInChunks(page, 5, CHUNK_MS); // 155s < 300s：全球卡还不到自己的节奏
    await realSleep(400);

    const coreAfter = await readMarker(coreIndexCard(page));
    const boardAfter = await readMarker(boardCard(page));
    console.log(`[拆分取证·开市155s] core ${coreBefore}→${coreAfter} board ${boardBefore}→${boardAfter}`);
    // A股核心指数卡：30s 节奏 → 155s 内至少 4 次新响应（给慢响应留余量）
    expect(coreAfter).toBeGreaterThanOrEqual(coreBefore + 2);
    // 全球指数卡：300s 未到 → 一次请求都没有（这正是「两张卡不再共用一份 30s 数据」的证据）
    expect(boardAfter).toBe(boardBefore);
  });

  test("休市 465s：全球指数卡按 300s 取新数据，A股核心指数卡一次都没刷", async ({ page }) => {
    const rows = await liveIndexRows(page);

    await page.clock.install({ time: CLOSED_SATURDAY_22H });
    await mockIndicesWithSeq(page, rows);
    await mountIndexCards(page);
    await expect15s(coreIndexCard(page)).toContainText(/#\d+/);
    await expect15s(boardCard(page)).toContainText(/#\d+/);

    const coreBefore = await readMarker(coreIndexCard(page));
    const boardBefore = await readMarker(boardCard(page));

    await advanceInChunks(page, 15, CHUNK_MS); // 465s：跨过全球卡的 300s 边界，仍未到 600s
    await realSleep(400);

    const coreAfter = await readMarker(coreIndexCard(page));
    const boardAfter = await readMarker(boardCard(page));
    const boardDelta = boardAfter - boardBefore;
    console.log(`[拆分取证·休市465s] core ${coreBefore}→${coreAfter} board ${boardBefore}→${boardAfter}`);
    // 全球盘：465s 内至少 1 次、最多 2 次新数据 → 300s 量级（不是 30s/60s）
    expect(boardDelta).toBeGreaterThanOrEqual(1);
    expect(boardDelta).toBeLessThanOrEqual(2);
    // A股核心指数卡：休市不轮询 → 数据冻结（不是「跟着全球卡一起刷」）
    expect(coreAfter).toBe(coreBefore);
  });

  test("节奏常量与卡片用法一致（防两处各写一个数字）", async () => {
    // 全球指数盘常驻慢轮询必须显著慢于盘中节奏，且为整分钟（可读、便于解释）
    expect(INDEX_POLL_INTERVAL_MS).toBe(300_000);
    expect(OPEN_POLL_INTERVAL_MS).toBe(30_000);
    expect(INDEX_POLL_INTERVAL_MS).toBeGreaterThan(OPEN_POLL_INTERVAL_MS);
  });
});
